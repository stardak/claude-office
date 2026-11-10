"""Tests for the Onset office adapter (POST /api/v1/onset/status)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.routes.events import reset_rate_limiter
from app.api.routes.onset import SESSION_ID, parse_staff
from app.config import get_settings
from app.core.event_processor import event_processor
from app.main import app
from app.models.agents import AgentState, BossState

client = TestClient(app)
KEY = "test-onset-key"
URL = "/api/v1/onset/status"


@pytest.fixture(autouse=True)
def onset_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(get_settings(), "ONSET_OFFICE_KEY", KEY)
    reset_rate_limiter()
    event_processor.sessions.pop(SESSION_ID, None)
    yield
    event_processor.sessions.pop(SESSION_ID, None)


def post(body: dict[str, object], key: str = KEY):  # type: ignore[no-untyped-def]
    return client.post(URL, json=body, headers={"X-Onset-Key": key})


def test_rejects_missing_or_wrong_key() -> None:
    assert post({"agent": "social"}, key="").status_code == 401
    assert post({"agent": "social"}, key="nope").status_code == 401


def test_disabled_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ONSET_OFFICE_KEY", "")
    assert post({"agent": "social"}).status_code == 503


@pytest.mark.parametrize(
    "task",
    [
        "Reply to jane@example.com",
        "Check https://example.com",
        "Revenue £1,200",
        "Customer 0012345",
        "Rotate the API key",
        "Trial to paid 12%",
        "x" * 61,
    ],
)
def test_privacy_filter_rejects_sensitive_labels(task: str) -> None:
    assert post({"agent": "email", "task": task}).status_code == 422


def test_seats_staff_with_exact_names_and_desks() -> None:
    r = post({"agent": "social", "status": "working", "task": "Drafting Wed carousel"})
    assert r.status_code == 200
    sm = event_processor.sessions[SESSION_ID]
    names = {aid: (a.name, a.desk) for aid, a in sm.agents.items()}
    assert names == {
        "social": ("Social", 1),
        "email": ("Email", 2),
        "ads": ("Ads", 3),
        "analyst": ("Analyst", 4),
        "creators": ("Creators", 5),
    }
    assert sm.agents["social"].state == AgentState.WORKING
    assert sm.agents["social"].current_task == "Drafting Wed carousel"
    assert sm.agents["social"].bubble is not None
    assert sm.agents["social"].bubble.text == "Drafting Wed carousel"


def test_approval_flag_and_clear() -> None:
    post({"agent": "email", "status": "waiting", "task": "Thu newsletter", "needs_approval": True})
    sm = event_processor.sessions[SESSION_ID]
    assert sm.agents["email"].state == AgentState.WAITING_PERMISSION
    post({"agent": "email", "status": "working", "task": "Scheduling Thu newsletter"})
    assert sm.agents["email"].state == AgentState.WORKING


def test_boss_approval_and_handoff() -> None:
    post({"agent": "onset", "status": "working", "task": "Plan week", "needs_approval": True})
    sm = event_processor.sessions[SESSION_ID]
    assert sm.boss_state == BossState.WAITING_PERMISSION
    post({"agent": "ads", "status": "done", "task": "Hook B won", "handoff_to": "social"})
    assert sm.agents["ads"].bubble is not None
    assert sm.agents["ads"].bubble.text == "→ Social: Hook B won"
    assert any("Ads → Social" in n.headline for n in sm.whiteboard.news_items)


def test_unknown_agent_rejected() -> None:
    r = post({"agent": "seo", "task": "Keyword sweep"})
    assert r.status_code == 422
    assert "Known:" in r.json()["detail"]
    assert post({"agent": "social", "handoff_to": "seo"}).status_code == 422


def test_team_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.routes.onset as onset_route

    monkeypatch.setattr(onset_route, "SESSION_ID", "onset_office_custom")
    monkeypatch.setattr(get_settings(), "ONSET_STAFF", "seo:SEO:🔍, social:Social:📸")
    assert post({"agent": "seo", "status": "working", "task": "Keyword sweep"}).status_code == 200
    sm = event_processor.sessions["onset_office_custom"]
    assert [(a.name, a.desk) for a in sm.agents.values()] == [("SEO", 1), ("Social", 2)]


def test_parse_staff_ignores_junk_and_caps_at_seven() -> None:
    spec = "onset:Boss, Bad Id:X, a, a:Dup, " + ", ".join(f"s{i}" for i in range(10))
    team = parse_staff(spec)
    assert "onset" not in team
    assert team["a"] == ("A", "💼")
    assert len(team) == 7
