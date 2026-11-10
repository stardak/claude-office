"""Tests for the company offices adapter (/api/v1/office/{id}/status, /api/v1/onset/status)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.routes.events import reset_rate_limiter
from app.api.routes.onset import parse_offices, parse_staff
from app.config import get_settings
from app.core.event_processor import event_processor
from app.main import app
from app.models.agents import AgentState, BossState

client = TestClient(app)
SESSION_ID = "onset_office"
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
    # Fresh office id so DB-restored desks from earlier tests don't interfere.
    monkeypatch.setattr(get_settings(), "OFFICES", "custom = seo:SEO:🔍, social:Social:📸")
    monkeypatch.setenv("OFFICE_KEY_CUSTOM", "k2")
    r = client.post(
        "/api/v1/office/custom/status",
        json={"agent": "seo", "status": "working", "task": "Keyword sweep"},
        headers={"X-Office-Key": "k2"},
    )
    assert r.status_code == 200
    sm = event_processor.sessions["custom_office"]
    assert [(a.name, a.desk) for a in sm.agents.values()] == [("SEO", 1), ("Social", 2)]


OFFICES = """
onset:Onset = social:Social:📸, email:Email:✉️;
hostmate:Hostmate = sales:Sales:🤝, support:Support:🎧
"""


def office_post(office: str, body: dict[str, object], key: str):  # type: ignore[no-untyped-def]
    return client.post(f"/api/v1/office/{office}/status", json=body, headers={"X-Office-Key": key})


def test_offices_are_separate_with_their_own_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "OFFICES", OFFICES)
    monkeypatch.setenv("OFFICE_KEY_HOSTMATE", "hm-key")
    # Onset's key can't drive Hostmate, and vice versa.
    assert office_post("hostmate", {"agent": "sales"}, KEY).status_code == 401
    assert office_post("onset", {"agent": "social"}, "hm-key").status_code == 401
    # Unknown office.
    assert office_post("nope", {"agent": "x"}, "hm-key").status_code == 404
    r = office_post("hostmate", {"agent": "hostmate", "task": "Plan the week"}, "hm-key")
    assert r.status_code == 200
    sm = event_processor.sessions["hostmate_office"]
    assert {a.name for a in sm.agents.values()} == {"Sales", "Support"}
    assert sm.boss_current_task == "Plan the week"
    # Onset staff aren't valid in Hostmate.
    assert office_post("hostmate", {"agent": "social"}, "hm-key").status_code == 422
    # Onset still works on the new route with its old key.
    assert office_post("onset", {"agent": "social", "task": "Carousel"}, KEY).status_code == 200


def test_office_without_key_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "OFFICES", OFFICES)
    monkeypatch.delenv("OFFICE_KEY_HOSTMATE", raising=False)
    assert office_post("hostmate", {"agent": "sales"}, "").status_code == 503


def test_parse_offices() -> None:
    offices = parse_offices(OFFICES + "\nBad Id = x; grace:Grace & Ashes = store, grace")
    assert list(offices) == ["onset", "hostmate", "grace"]
    assert offices["grace"].title == "Grace & Ashes"
    assert list(offices["grace"].staff) == ["store"]  # boss id isn't a staff desk
    assert offices["hostmate"].session_id == "hostmate_office"


def test_parse_staff_ignores_junk_and_caps_at_seven() -> None:
    spec = "Bad Id:X, a, a:Dup, " + ", ".join(f"s{i}" for i in range(10))
    team = parse_staff(spec)
    assert team["a"] == ("A", "💼")
    assert len(team) == 7
