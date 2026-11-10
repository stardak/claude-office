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
    ["password: hunter2hunter2", "api_key=abc123def456", "cfast_ApGUystLw88JbQKlB3J5IX", "x" * 61],
)
def test_rejects_secrets_and_long_labels(task: str) -> None:
    assert post({"agent": "email", "task": task}).status_code == 422


@pytest.mark.parametrize(
    "task",
    ["Reply to jane@example.com", "Revenue £1,200", "Trial to paid 12%", "Rotate the API key"],
)
def test_allows_content_and_figures(task: str) -> None:
    assert post({"agent": "email", "task": task}).status_code == 200


def test_finished_work_is_saved_and_listed() -> None:
    body = {
        "agent": "email",
        "status": "waiting",
        "task": "Thu newsletter",
        "needs_approval": True,
        "detail": "Subject: New 30-day arms challenge\n\nHi there...",
        "link": "https://app.brevo.com/campaign/edit/42",
    }
    assert post(body).status_code == 200
    assert post({**body, "task": "Thu newsletter v2", "detail": "v2"}).status_code == 200
    r = client.get(f"/api/v1/office-work/{SESSION_ID}/email")
    assert r.status_code == 200
    items = r.json()
    assert [i["title"] for i in items[:2]] == ["Thu newsletter v2", "Thu newsletter"]
    assert items[1]["detail"].startswith("Subject: New 30-day")
    assert items[1]["link"] == "https://app.brevo.com/campaign/edit/42"
    assert items[1]["needsApproval"] is True
    # Boss work is stored under "boss" and "main" is accepted as an alias.
    assert post({"agent": "onset", "task": "Plan", "detail": "Week plan"}).status_code == 200
    assert client.get(f"/api/v1/office-work/{SESSION_ID}/main").json()[0]["detail"] == "Week plan"


def test_finished_work_rejects_secrets_and_bad_links() -> None:
    assert post({"agent": "email", "detail": "password = hunter2hunter2"}).status_code == 422
    assert post({"agent": "email", "link": "javascript:alert(1)"}).status_code == 422
    assert post({"agent": "email", "link": "http://insecure.example"}).status_code == 422


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


def test_parse_staff_ignores_junk_and_caps_at_eight() -> None:
    spec = "Bad Id:X, a, a:Dup, " + ", ".join(f"s{i}" for i in range(10))
    team = parse_staff(spec)
    assert team["a"] == ("A", "💼")
    assert len(team) == 8


def test_work_tab_lists_all_agents_including_bare_approvals() -> None:
    assert post({"agent": "social", "task": "Reel plan", "needs_approval": True}).status_code == 200
    assert post({"agent": "analyst", "task": "Report", "detail": "All good"}).status_code == 200
    assert post({"agent": "ads", "task": "Just working"}).status_code == 200  # not saved
    items = client.get(f"/api/v1/office-work/{SESSION_ID}").json()
    keys = [(i["agentKey"], i["title"]) for i in items[:2]]
    assert keys == [("analyst", "Report"), ("social", "Reel plan")]
    assert items[1]["detail"] is None and items[1]["needsApproval"] is True
    assert all(i["title"] != "Just working" for i in items)


def test_chat_round_trip() -> None:
    # Stef briefs the Analyst from the office (no bot key needed: behind Access).
    r = client.post(
        f"/api/v1/office-chat/{SESSION_ID}/analyst", json={"text": "Focus on trial to paid"}
    )
    assert r.status_code == 200
    # Bot collects it with its key; second call is empty (marked read).
    inbox = client.get("/api/v1/office/onset/inbox/analyst", headers={"X-Office-Key": KEY})
    assert [m["text"] for m in inbox.json()] == ["Focus on trial to paid"]
    again = client.get("/api/v1/office/onset/inbox/analyst", headers={"X-Office-Key": KEY})
    assert again.json() == []
    # Wrong key can't read the inbox.
    bad = client.get("/api/v1/office/onset/inbox/analyst", headers={"X-Office-Key": "x"})
    assert bad.status_code == 401
    # Bot replies via its status update; thread shows both sides in order.
    body = {"agent": "analyst", "task": "Brief received", "reply": "On it, report by Friday"}
    assert post(body).status_code == 200
    thread = client.get(f"/api/v1/office-chat/{SESSION_ID}/analyst").json()
    assert [(m["sender"], m["text"]) for m in thread[-2:]] == [
        ("stef", "Focus on trial to paid"),
        ("bot", "On it, report by Friday"),
    ]
    # Boss chat works via "main"/"boss"/office id.
    client.post(f"/api/v1/office-chat/{SESSION_ID}/main", json={"text": "Plan the week"})
    boss_inbox = client.get("/api/v1/office/onset/inbox/onset", headers={"X-Office-Key": KEY})
    assert [m["text"] for m in boss_inbox.json()] == ["Plan the week"]


def test_chat_rejects_unknown_agent_and_secrets() -> None:
    assert (
        client.post(f"/api/v1/office-chat/{SESSION_ID}/nobody", json={"text": "hi"}).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/office-chat/{SESSION_ID}/email", json={"text": "password: hunter2hunter2"}
        ).status_code
        == 422
    )


def test_inbox_accepts_onset_key_header_and_onset_route() -> None:
    client.post(f"/api/v1/office-chat/{SESSION_ID}/email", json={"text": "Shorter subject"})
    peek = client.get("/api/v1/office/onset/inbox/email?peek=true", headers={"X-Onset-Key": KEY})
    assert peek.status_code == 200
    assert [m["text"] for m in peek.json()] == ["Shorter subject"]
    got = client.get("/api/v1/onset/inbox/email", headers={"X-Onset-Key": KEY})
    assert [(m["sender"], m["text"]) for m in got.json()] == [("stef", "Shorter subject")]
    assert set(got.json()[0]) == {"id", "sender", "text", "read", "createdAt"}
    assert client.get("/api/v1/onset/inbox/email", headers={"X-Onset-Key": KEY}).json() == []
    # Status on the office route also accepts X-Onset-Key.
    r = client.post(
        "/api/v1/office/onset/status", json={"agent": "email"}, headers={"X-Onset-Key": KEY}
    )
    assert r.status_code == 200


# --- Questions / answers / manager inbox --------------------------------------

ONSET_HEADERS = {"X-Onset-Key": KEY}
Q = {
    "id": "q-2026-11-10-001",
    "text": "Post at 12:00 or 19:00 Lisbon?",
    "options": ["12:00", "19:00"],
    "allow_text": True,
    "link": "https://example.com/approvals",
}


def _questions() -> list[dict[str, object]]:
    return client.get(f"/api/v1/office-questions/{SESSION_ID}").json()


def _answer(qid: str, answer: str, headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/v1/office-questions/{SESSION_ID}/{qid}/answer",
        json={"answer": answer},
        headers=headers or {},
    )


def test_question_posts_shows_and_upserts() -> None:
    body = {"agent": "social", "task": "Scheduling reel", "question": Q}
    assert post(body).status_code == 200
    assert post({**body, "question": {**Q, "text": "Post at 12:00 or 20:00?"}}).status_code == 200
    mine = [q for q in _questions() if q["id"] == Q["id"]]
    assert len(mine) == 1  # same id updates, no duplicate
    assert mine[0]["agent"] == "social"
    assert mine[0]["text"] == "Post at 12:00 or 20:00?"
    assert mine[0]["options"] == ["12:00", "19:00"]
    assert mine[0]["link"] == "https://example.com/approvals"


@pytest.mark.parametrize(
    "bad",
    [
        {**Q, "text": "x" * 201},
        {**Q, "options": ["a"] * 7},
        {**Q, "options": ["x" * 41]},
        {**Q, "link": "http://insecure.example"},
        {**Q, "id": "x" * 65},
    ],
)
def test_question_validation(bad: dict[str, object]) -> None:
    assert post({"agent": "social", "question": bad}).status_code == 422


def test_full_question_round_trip_and_inbox(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.access_identity as ident

    qid = "q-roundtrip-1"
    post({"agent": "email", "task": "Newsletter", "question": {**Q, "id": qid}})

    # Behind Cloudflare: signed JWT required; service tokens (no email) refused.
    monkeypatch.setattr(get_settings(), "ACCESS_TEAM_DOMAIN", "https://team.example")
    monkeypatch.setattr(get_settings(), "ANSWER_EMAILS", "stef@example.com")
    claims_by_token = {
        "human": {"email": "stef@example.com"},
        "bot": {"common_name": "abc.access"},
        "other": {"email": "someone@else.com"},
    }
    monkeypatch.setattr(ident, "_verify", lambda token, team, aud: claims_by_token[token])
    assert _answer(qid, "12:00").status_code == 401
    bot = {"Cf-Access-Jwt-Assertion": "bot"}
    assert _answer(qid, "12:00", bot).status_code == 403
    assert _answer(qid, "12:00", {"Cf-Access-Jwt-Assertion": "other"}).status_code == 403
    human = {"Cf-Access-Jwt-Assertion": "human"}
    r = _answer(qid, "12:00", human)
    assert r.status_code == 200
    assert r.json()["answer"] == "12:00" and r.json()["answeredBy"] == "stef@example.com"
    # Can change it before onset collects.
    assert _answer(qid, "Neither, 18:30 please", human).status_code == 200

    # Inbox needs the key.
    assert client.get("/api/v1/onset/inbox").status_code == 401
    inbox = client.get("/api/v1/onset/inbox", headers=ONSET_HEADERS)
    assert inbox.status_code == 200
    item = next(i for i in inbox.json() if i["id"] == qid)
    assert item == {
        "id": qid,
        "agent": "email",
        "question_text": Q["text"],
        "answer": "Neither, 18:30 please",
        "answered_at": item["answered_at"],
        "answered_by": "stef@example.com",
    }
    assert item["answered_at"].endswith("Z")
    # since in the future filters it out.
    later = client.get("/api/v1/onset/inbox?since=2099-01-01T00:00:00Z", headers=ONSET_HEADERS)
    assert all(i["id"] != qid for i in later.json())

    ack = client.post("/api/v1/onset/inbox/ack", json={"ids": [qid]}, headers=ONSET_HEADERS)
    assert ack.status_code == 200 and ack.json()["acked"] == 1
    again = client.get("/api/v1/onset/inbox", headers=ONSET_HEADERS).json()
    assert all(i["id"] != qid for i in again)
    # Locked once collected.
    assert _answer(qid, "19:00", human).status_code == 409
    # A bot token can't send briefs either.
    brief = client.post(
        f"/api/v1/office-chat/{SESSION_ID}/analyst", json={"text": "hi"}, headers=bot
    )
    assert brief.status_code == 403


def test_briefs_come_through_manager_inbox_and_ack() -> None:
    client.post(f"/api/v1/office-chat/{SESSION_ID}/analyst", json={"text": "Focus on trials"})
    items = client.get("/api/v1/onset/inbox", headers=ONSET_HEADERS).json()
    brief = next(i for i in items if i["answer"] == "Focus on trials")
    assert brief["agent"] == "stef" and brief["to"] == "analyst"
    assert brief["id"].startswith("brief-")
    client.post("/api/v1/onset/inbox/ack", json={"ids": [brief["id"]]}, headers=ONSET_HEADERS)
    items = client.get("/api/v1/onset/inbox", headers=ONSET_HEADERS).json()
    assert all(i["id"] != brief["id"] for i in items)


def test_close_question_and_option_only_answers() -> None:
    qid = "q-close-1"
    post({"agent": "ads", "question": {"id": qid, "text": "Budget A or B?", "options": ["A", "B"]}})
    assert _answer(qid, "C").status_code == 422  # options only, no free text
    assert _answer(qid, "A").status_code == 200
    assert client.post("/api/v1/onset/question/close", json={"id": qid}).status_code == 401
    r = client.post("/api/v1/onset/question/close", json={"id": qid}, headers=ONSET_HEADERS)
    assert r.status_code == 200
    assert all(q["id"] != qid for q in _questions())
    assert _answer(qid, "B").status_code == 404
