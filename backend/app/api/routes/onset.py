"""Company offices adapter.

One small endpoint that lets Stef's bots drive an office with a single line of
JSON instead of Claude Code hook events. Each company gets its own office
(its own session in the left-hand list), team and key:

    POST /api/v1/office/{office}/status        e.g. /api/v1/office/hostmate/status
    X-Office-Key: <OFFICE_KEY_HOSTMATE>
    {"agent": "sales", "status": "working", "task": "Onboarding new vendor",
     "needs_approval": false, "handoff_to": null,
     "detail": "optional: the finished work itself (email text, report...)",
     "link": "optional: https link to it (Brevo draft, Google Doc...)"}

When ``detail`` or ``link`` is sent, it's saved as that agent's finished work
and shown when Stef clicks the agent (GET /api/v1/office-work/{session}/{agent}).

The legacy POST /api/v1/onset/status (X-Onset-Key: ONSET_OFFICE_KEY) still
drives the "onset" office.

Offices and teams come from OFFICES (fly.toml), one office per line or ";":

    onset:Onset = social:Social:📸, email:Email:✉️, ads:Ads:📣
    hostmate:Hostmate = sales:Sales:🤝, support:Support:🎧

The boss of each office is the office id ("onset", "hostmate"); staff sit in
listed order (max 8, the office has 8 staff desks). Keys are Fly secrets named
OFFICE_KEY_<ID>, so a leaked key for one company can't touch another office.

The office sits behind Cloudflare Access, so content, customer data and
figures are allowed. Secrets aren't: anything that looks like a password, API
key or token is rejected, in labels and in finished work.
"""

import hmac
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select

from app.api.routes.events import _check_rate_limit  # pyright: ignore[reportPrivateUsage]
from app.config import get_settings
from app.core.access_identity import require_human
from app.core.broadcast_service import broadcast_state
from app.core.event_processor import EventProcessor, get_event_processor
from app.db.database import get_session_factory
from app.db.models import OfficeMessage, OfficeQuestion, OfficeWork
from app.models.common import BubbleContent, BubbleType
from app.models.events import EventAdapter

router = APIRouter()

STAFF_TYPE = "onset-staff"  # summary_service keeps these names verbatim
MAX_STAFF = 8  # 8 staff desks (the boss has its own)
_ID = r"[a-z][a-z0-9-]{0,23}"
_ID_RE = re.compile(_ID)

MAX_DETAIL = 20_000
MAX_MESSAGE = 4_000
KEEP_WORK = 20  # finished-work items kept per agent
# Secrets never go in the office: "password: x", "api_key=...", bearer tokens,
# common key formats (sk-..., cfast_..., ghp_..., AKIA...).
_SECRET_RE = re.compile(
    r"\b(api[ _-]?key|secret|password|passwd|token)\s*[:=]\s*\S+"
    r"|\bbearer\s+[a-z0-9._-]{16,}"
    r"|\b(sk|rk|pk)-[a-z0-9_-]{16,}|\bcfast_[a-z0-9]{16,}|\bghp_[a-z0-9]{20,}|\bAKIA[0-9A-Z]{16}",
    re.I,
)


@dataclass(frozen=True)
class Office:
    id: str
    title: str  # shown in the sessions list
    staff: dict[str, tuple[str, str]]  # agent id -> (display name, icon), desk order

    @property
    def session_id(self) -> str:
        return f"{self.id}_office"


def parse_staff(spec: str) -> dict[str, tuple[str, str]]:
    """Parse "id:Name:icon, id:Name:icon" into {id: (Name, icon)}, in order."""
    out: dict[str, tuple[str, str]] = {}
    for item in spec.split(","):
        parts = [p.strip() for p in item.split(":")]
        agent_id = parts[0].lower()
        if not _ID_RE.fullmatch(agent_id) or agent_id in out:
            continue
        name = (parts[1] if len(parts) > 1 and parts[1] else agent_id.capitalize())[:20]
        icon = parts[2] if len(parts) > 2 and parts[2] else "💼"
        out[agent_id] = (name, icon)
        if len(out) == MAX_STAFF:
            break
    return out


def parse_offices(spec: str) -> dict[str, Office]:
    """Parse OFFICES: lines/';' of "id[:Title] = staff spec"."""
    result: dict[str, Office] = {}
    for line in re.split(r"[;\n]", spec):
        if "=" not in line:
            continue
        head, team = line.split("=", 1)
        office_id, _, title = (p.strip() for p in head.partition(":"))
        office_id = office_id.lower()
        if not _ID_RE.fullmatch(office_id) or office_id in result:
            continue
        staff = {k: v for k, v in parse_staff(team).items() if k != office_id}
        result[office_id] = Office(office_id, (title or office_id.capitalize())[:30], staff)
    return result


def offices() -> dict[str, Office]:
    return parse_offices(get_settings().OFFICES)


def _office_key(office_id: str) -> str:
    key = os.environ.get(f"OFFICE_KEY_{office_id.upper().replace('-', '_')}", "")
    if not key and office_id == "onset":
        key = get_settings().ONSET_OFFICE_KEY
    return key


def _check_key(office_id: str, provided: str) -> None:
    expected = _office_key(office_id)
    if not expected:
        raise HTTPException(status_code=503, detail=f"Office '{office_id}' has no key set")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid office key")


class QuestionIn(BaseModel):
    """A question a bot asks Stef (shown on the Work tab, answered there)."""

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")
    text: str = Field(min_length=1, max_length=200)
    options: list[str] = Field(default_factory=list, max_length=6)
    allow_text: bool = False
    link: str | None = Field(default=None, max_length=500, pattern=r"^https://\S+$")

    @field_validator("options")
    @classmethod
    def short_options(cls, v: list[str]) -> list[str]:
        out = [" ".join(o.split()) for o in v]
        if any(not o or len(o) > 40 for o in out):
            raise ValueError("options must be 1-40 characters each")
        if any(_SECRET_RE.search(o) for o in out):
            raise ValueError("rejected: looks like a secret")
        return out

    @field_validator("text")
    @classmethod
    def no_secret_text(cls, v: str) -> str:
        if _SECRET_RE.search(v):
            raise ValueError("rejected: looks like a secret")
        return " ".join(v.split())


class OfficeStatus(BaseModel):
    agent: str = Field(pattern=rf"^{_ID}$")
    status: Literal["working", "waiting", "idle", "done"] = "working"
    task: str = Field(default="", max_length=60)
    needs_approval: bool = False
    handoff_to: str | None = Field(default=None, pattern=rf"^{_ID}$")
    detail: str | None = Field(default=None, max_length=MAX_DETAIL)
    reply: str | None = Field(default=None, max_length=MAX_MESSAGE)
    link: str | None = Field(default=None, max_length=500, pattern=r"^https://\S+$")
    question: QuestionIn | None = None

    @field_validator("task")
    @classmethod
    def short_label(cls, v: str) -> str:
        v = " ".join(v.split())
        if _SECRET_RE.search(v):
            raise ValueError("task label rejected: looks like a secret")
        return v

    @field_validator("detail", "reply")
    @classmethod
    def no_secrets(cls, v: str | None) -> str | None:
        if v and _SECRET_RE.search(v):
            raise ValueError("rejected: looks like it contains a secret")
        return v


async def _send(ep: EventProcessor, office: Office, event_type: str, **data: Any) -> None:
    event = EventAdapter.validate_python(
        {
            "event_type": event_type,
            "session_id": office.session_id,
            "timestamp": datetime.now(UTC),
            "data": {"project_name": office.title, **data},
        }
    )
    await ep.process_event(event)


async def _ensure_office(ep: EventProcessor, office: Office) -> None:
    """Open the office and seat any configured staff not yet at a desk, in order."""
    await ep.get_current_state(office.session_id)  # restores from DB after a restart
    sm = ep.sessions.get(office.session_id)
    if sm is None or sm.phase.name in ("EMPTY", "ENDED"):
        await _send(ep, office, "session_start")
        sm = ep.sessions.get(office.session_id)
    seated: set[str] = set(sm.agents) if sm else set()
    for agent_id, (name, _icon) in office.staff.items():
        if agent_id not in seated:
            await _send(
                ep,
                office,
                "subagent_start",
                agent_id=agent_id,
                agent_name=name,
                agent_type=STAFF_TYPE,
                task_description=f"{name} desk",
            )


def _bubble(text: str, icon: str) -> BubbleContent:
    return BubbleContent(type=BubbleType.THOUGHT, text=text[:60], icon=icon)


async def _apply(ep: EventProcessor, office: Office, body: OfficeStatus) -> dict[str, str]:
    team = office.staff
    for who in (body.agent, body.handoff_to):
        if who and who != office.id and who not in team:
            known = ", ".join([office.id, *team])
            raise HTTPException(status_code=422, detail=f"Unknown agent '{who}'. Known: {known}")
    _check_rate_limit(office.session_id)
    await _ensure_office(ep, office)
    task = body.task or body.status.capitalize()
    is_boss = body.agent == office.id
    agent_id = None if is_boss else body.agent
    icon = "🧠" if is_boss else team[body.agent][1]

    def name_of(who: str) -> str:
        return office.id if who == office.id else team[who][0]

    # 1. Drive the real state machine with repo events.
    if body.needs_approval:
        await _send(
            ep, office, "permission_request", agent_id=agent_id, tool_name=f"Approval: {task}"
        )
        bubble = _bubble(f"Needs your OK: {task}", "🚨")
    elif body.status == "working":
        # post first clears any pending approval, then pre marks it working.
        await _send(ep, office, "post_tool_use", agent_id=agent_id, tool_name=task)
        await _send(ep, office, "pre_tool_use", agent_id=agent_id, tool_name=task)
        bubble = _bubble(task, icon)
    else:
        await _send(ep, office, "post_tool_use", agent_id=agent_id, tool_name=task)
        label = {"waiting": "Waiting", "idle": "Idle", "done": "Done"}[body.status]
        bubble = _bubble(
            f"{label}: {body.task}" if body.task else label,
            {"waiting": "⏳", "idle": "☕", "done": "✅"}[body.status],
        )

    # 2. Cosmetic touches the hook events can't express (bubble, monitor, handoff).
    sm = ep.sessions.get(office.session_id)
    if sm is not None:
        if body.handoff_to and body.handoff_to != body.agent:
            to_name, from_name = name_of(body.handoff_to), name_of(body.agent)
            bubble = _bubble(f"→ {to_name}: {task}", "🤝")
            sm.whiteboard.add_news_item("agent", f"{from_name} → {to_name}: {task}")
        if is_boss:
            sm.boss_bubble = bubble
            if body.task:
                sm.boss_current_task = body.task
        elif body.agent in sm.agents:
            sm.agents[body.agent].bubble = bubble
            if body.task:
                sm.agents[body.agent].current_task = body.task
        await broadcast_state(office.session_id, sm)

    agent_key = "boss" if is_boss else body.agent
    # 3. Finished work (or an approval request) saved for the Work tab.
    if body.detail or body.link or body.needs_approval:
        await _save_work(office, agent_key, task, body)
    # 4. A chat reply to Stef.
    if body.reply:
        await _add_message(office.session_id, agent_key, "bot", body.reply)
    # 5. A question for Stef (upserted by its id).
    if body.question:
        await _upsert_question(office, agent_key, body.question)
        if sm is not None:
            q_bubble = _bubble(f"Question: {body.question.text}", "❓")
            if is_boss:
                sm.boss_bubble = q_bubble
            elif body.agent in sm.agents:
                sm.agents[body.agent].bubble = q_bubble
            await broadcast_state(office.session_id, sm)

    return {"status": "ok", "office": office.id, "session_id": office.session_id}


async def _save_work(office: Office, agent_key: str, title: str, body: OfficeStatus) -> None:
    async with get_session_factory()() as db:
        db.add(
            OfficeWork(
                session_id=office.session_id,
                agent_key=agent_key,
                title=title,
                status="needs approval" if body.needs_approval else body.status,
                needs_approval=body.needs_approval,
                detail=body.detail,
                link=body.link,
            )
        )
        await db.flush()
        keep = (
            select(OfficeWork.id)
            .where(OfficeWork.session_id == office.session_id)
            .where(OfficeWork.agent_key == agent_key)
            .order_by(OfficeWork.id.desc())
            .limit(KEEP_WORK)
        )
        await db.execute(
            delete(OfficeWork)
            .where(OfficeWork.session_id == office.session_id)
            .where(OfficeWork.agent_key == agent_key)
            .where(OfficeWork.id.not_in(keep))
        )
        await db.commit()


def _work_json(w: OfficeWork) -> dict[str, Any]:
    return {
        "id": w.id,
        "agentKey": w.agent_key,
        "title": w.title,
        "status": w.status,
        "needsApproval": w.needs_approval,
        "detail": w.detail,
        "link": w.link,
        "createdAt": w.created_at.isoformat(),
    }


@router.get("/office-work/{session_id}")
async def office_work_all(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """All finished work in one office, newest first (the Work tab)."""
    async with get_session_factory()() as db:
        rows = (
            await db.execute(
                select(OfficeWork)
                .where(OfficeWork.session_id == session_id)
                .order_by(OfficeWork.id.desc())
                .limit(max(1, min(limit, 200)))
            )
        ).scalars()
        return [_work_json(w) for w in rows]


@router.get("/office-work/{session_id}/{agent_key}")
async def office_work(session_id: str, agent_key: str, limit: int = 10) -> list[dict[str, Any]]:
    """Finished work for one agent, newest first (read by the agent popup)."""
    if agent_key == "main":
        agent_key = "boss"
    async with get_session_factory()() as db:
        rows = (
            await db.execute(
                select(OfficeWork)
                .where(OfficeWork.session_id == session_id)
                .where(OfficeWork.agent_key == agent_key)
                .order_by(OfficeWork.id.desc())
                .limit(max(1, min(limit, KEEP_WORK)))
            )
        ).scalars()
        return [_work_json(w) for w in rows]


def _get_office(office_id: str) -> Office:
    office = offices().get(office_id)
    if office is None:
        raise HTTPException(status_code=404, detail=f"No office '{office_id}'")
    return office


@router.post("/office/{office_id}/status")
async def office_status(
    office_id: str,
    body: OfficeStatus,
    ep: Annotated[EventProcessor, Depends(get_event_processor)],
    x_office_key: Annotated[str, Header()] = "",
    x_onset_key: Annotated[str, Header()] = "",
) -> dict[str, str]:
    office = _get_office(office_id)
    _check_key(office.id, x_office_key or x_onset_key)
    return await _apply(ep, office, body)


@router.post("/onset/status")
async def onset_status(
    body: OfficeStatus,
    ep: Annotated[EventProcessor, Depends(get_event_processor)],
    x_onset_key: Annotated[str, Header()] = "",
) -> dict[str, str]:
    office = _get_office("onset")
    _check_key(office.id, x_onset_key)
    return await _apply(ep, office, body)


# --- Chat: Stef <-> one agent -------------------------------------------------


def _msg_json(m: OfficeMessage) -> dict[str, Any]:
    return {
        "id": m.id,
        "sender": m.sender,
        "text": m.text,
        "read": m.read_at is not None,
        "createdAt": m.created_at.isoformat(),
    }


async def _add_message(
    session_id: str, agent_key: str, sender: str, text: str, sent_by: str | None = None
) -> OfficeMessage:
    async with get_session_factory()() as db:
        msg = OfficeMessage(
            session_id=session_id,
            agent_key=agent_key,
            sender=sender,
            text=text,
            sent_by=sent_by,
        )
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
        return msg


def _chat_key(agent_key: str) -> str:
    return "boss" if agent_key == "main" else agent_key


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE)

    @field_validator("text")
    @classmethod
    def no_secrets(cls, v: str) -> str:
        if _SECRET_RE.search(v):
            raise ValueError("rejected: looks like it contains a secret")
        return v.strip()


@router.get("/office-chat/{session_id}/{agent_key}")
async def chat_thread(session_id: str, agent_key: str, limit: int = 50) -> list[dict[str, Any]]:
    """Stef's chat with one agent, oldest first (the office popup)."""
    async with get_session_factory()() as db:
        rows = list(
            (
                await db.execute(
                    select(OfficeMessage)
                    .where(OfficeMessage.session_id == session_id)
                    .where(OfficeMessage.agent_key == _chat_key(agent_key))
                    .order_by(OfficeMessage.id.desc())
                    .limit(max(1, min(limit, 200)))
                )
            ).scalars()
        )
        return [_msg_json(m) for m in reversed(rows)]


@router.post("/office-chat/{session_id}/{agent_key}")
async def chat_send(
    session_id: str, agent_key: str, body: ChatIn, request: Request
) -> dict[str, Any]:
    """Stef sends a message/brief to one agent. It waits in the bot's inbox.

    Only Stef's Cloudflare Access login may send (not a bot's service token).
    """
    who = await require_human(request)
    office_id = session_id.removesuffix("_office")
    office = offices().get(office_id)
    key = _chat_key(agent_key)
    if office is None or not session_id.endswith("_office"):
        raise HTTPException(status_code=404, detail="Not an office")
    if key != "boss" and key not in office.staff:
        raise HTTPException(status_code=404, detail=f"No agent '{agent_key}' in {office.title}")
    return _msg_json(await _add_message(session_id, key, "stef", body.text, sent_by=who))


@router.get("/office/{office_id}/inbox/{agent}")
async def bot_inbox(
    office_id: str,
    agent: str,
    x_office_key: Annotated[str, Header()] = "",
    x_onset_key: Annotated[str, Header()] = "",
    peek: bool = False,
) -> list[dict[str, Any]]:
    """Bots: collect unread messages from Stef (marked read unless ?peek=true).

    Accepts the office key as X-Office-Key or X-Onset-Key. Use agent "boss"
    (or the office id) for the office's manager bot. Returns a list of
    {id, sender: "stef", text, read, createdAt}, oldest first.
    """
    office = _get_office(office_id)
    _check_key(office.id, x_office_key or x_onset_key)
    key = "boss" if agent in ("boss", "main", office.id) else agent
    async with get_session_factory()() as db:
        rows = list(
            (
                await db.execute(
                    select(OfficeMessage)
                    .where(OfficeMessage.session_id == office.session_id)
                    .where(OfficeMessage.agent_key == key)
                    .where(OfficeMessage.sender == "stef")
                    .where(OfficeMessage.read_at.is_(None))
                    .order_by(OfficeMessage.id)
                )
            ).scalars()
        )
        if not peek:
            now = datetime.now(UTC)
            for m in rows:
                m.read_at = now
            await db.commit()
        return [_msg_json(m) for m in rows]


@router.get("/onset/inbox/{agent}")
async def onset_inbox(
    agent: str,
    x_onset_key: Annotated[str, Header()] = "",
    x_office_key: Annotated[str, Header()] = "",
    peek: bool = False,
) -> list[dict[str, Any]]:
    """Onset bots: same as /office/onset/inbox/{agent}, matching /onset/status."""
    return await bot_inbox(
        "onset", agent, x_office_key=x_office_key, x_onset_key=x_onset_key, peek=peek
    )


# --- Questions: bots ask, Stef answers, onset collects ------------------------

RETENTION = timedelta(days=90)
MAX_ANSWER = 500


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:  # SQLite returns naive UTC
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _q_json(q: OfficeQuestion) -> dict[str, Any]:
    return {
        "id": q.qid,
        "agent": q.agent_key,
        "text": q.text,
        "options": list(q.options or []),
        "allowText": q.allow_text,
        "link": q.link,
        "status": q.status,
        "answer": q.answer,
        "answeredAt": _iso(q.answered_at),
        "answeredBy": q.answered_by,
        "collected": q.collected_at is not None,
        "createdAt": _iso(q.created_at),
    }


async def _prune(db: Any) -> None:
    """Keep the question/answer log and briefs for 90 days."""
    cutoff = datetime.now(UTC) - RETENTION
    await db.execute(delete(OfficeQuestion).where(OfficeQuestion.updated_at < cutoff))
    await db.execute(delete(OfficeMessage).where(OfficeMessage.created_at < cutoff))


async def _upsert_question(office: Office, agent_key: str, q: QuestionIn) -> None:
    now = datetime.now(UTC)
    async with get_session_factory()() as db:
        row = (
            await db.execute(
                select(OfficeQuestion)
                .where(OfficeQuestion.session_id == office.session_id)
                .where(OfficeQuestion.qid == q.id)
            )
        ).scalar_one_or_none()
        if row is None:
            row = OfficeQuestion(session_id=office.session_id, qid=q.id, created_at=now)
            db.add(row)
        row.agent_key = agent_key
        row.text = q.text
        row.options = q.options
        row.allow_text = q.allow_text
        row.link = q.link
        if row.status == "closed":
            row.status = "answered" if row.answer else "open"
        row.updated_at = now
        await _prune(db)
        await db.commit()


@router.get("/office-questions/{session_id}")
async def office_questions(session_id: str) -> list[dict[str, Any]]:
    """Open and answered questions in one office, newest first (the Work tab)."""
    async with get_session_factory()() as db:
        rows = (
            await db.execute(
                select(OfficeQuestion)
                .where(OfficeQuestion.session_id == session_id)
                .where(OfficeQuestion.status != "closed")
                .order_by(OfficeQuestion.updated_at.desc())
                .limit(100)
            )
        ).scalars()
        return [_q_json(q) for q in rows]


class AnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=MAX_ANSWER)

    @field_validator("answer")
    @classmethod
    def clean(cls, v: str) -> str:
        if _SECRET_RE.search(v):
            raise ValueError("rejected: looks like a secret")
        return v.strip()


@router.post("/office-questions/{session_id}/{qid}/answer")
async def answer_question(
    session_id: str, qid: str, body: AnswerIn, request: Request
) -> dict[str, Any]:
    """Stef answers a question. Only his Cloudflare Access login, never a bot token.

    He can change the answer until onset has collected it.
    """
    who = await require_human(request)
    async with get_session_factory()() as db:
        row = (
            await db.execute(
                select(OfficeQuestion)
                .where(OfficeQuestion.session_id == session_id)
                .where(OfficeQuestion.qid == qid)
            )
        ).scalar_one_or_none()
        if row is None or row.status == "closed":
            raise HTTPException(status_code=404, detail="No open question with that id")
        if row.collected_at is not None:
            raise HTTPException(status_code=409, detail="Already collected by onset")
        if row.options and body.answer not in row.options and not row.allow_text:
            raise HTTPException(status_code=422, detail="Pick one of the options")
        now = datetime.now(UTC)
        row.answer = body.answer
        row.answered_at = now
        row.answered_by = who
        row.status = "answered"
        row.updated_at = now
        await db.commit()
        await db.refresh(row)
        return _q_json(row)


class CloseIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)


async def _close_question(office: Office, qid: str) -> dict[str, str]:
    async with get_session_factory()() as db:
        row = (
            await db.execute(
                select(OfficeQuestion)
                .where(OfficeQuestion.session_id == office.session_id)
                .where(OfficeQuestion.qid == qid)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="No question with that id")
        row.status = "closed"
        row.updated_at = datetime.now(UTC)
        await db.commit()
    return {"status": "ok", "id": qid}


@router.post("/onset/question/close")
async def onset_question_close(
    body: CloseIn,
    x_onset_key: Annotated[str, Header()] = "",
    x_office_key: Annotated[str, Header()] = "",
) -> dict[str, str]:
    office = _get_office("onset")
    _check_key(office.id, x_onset_key or x_office_key)
    return await _close_question(office, body.id)


@router.post("/office/{office_id}/question/close")
async def office_question_close(
    office_id: str,
    body: CloseIn,
    x_office_key: Annotated[str, Header()] = "",
    x_onset_key: Annotated[str, Header()] = "",
) -> dict[str, str]:
    office = _get_office(office_id)
    _check_key(office.id, x_office_key or x_onset_key)
    return await _close_question(office, body.id)


def _parse_since(since: str | None) -> datetime | None:
    if not since:
        return None
    try:
        dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="since must be ISO 8601") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _aware(dt: datetime | None) -> datetime | None:
    return None if dt is None else (dt if dt.tzinfo else dt.replace(tzinfo=UTC))


async def _manager_inbox(office: Office, since: str | None) -> list[dict[str, Any]]:
    """Answered-but-uncollected questions plus Stef's uncollected briefs."""
    after = _parse_since(since)
    async with get_session_factory()() as db:
        qs = (
            await db.execute(
                select(OfficeQuestion)
                .where(OfficeQuestion.session_id == office.session_id)
                .where(OfficeQuestion.status == "answered")
                .where(OfficeQuestion.collected_at.is_(None))
                .order_by(OfficeQuestion.answered_at)
            )
        ).scalars()
        briefs = (
            await db.execute(
                select(OfficeMessage)
                .where(OfficeMessage.session_id == office.session_id)
                .where(OfficeMessage.sender == "stef")
                .where(OfficeMessage.read_at.is_(None))
                .order_by(OfficeMessage.id)
            )
        ).scalars()
        out: list[dict[str, Any]] = []
        for q in qs:
            at = _aware(q.answered_at)
            if after and at and at < after:
                continue
            out.append(
                {
                    "id": q.qid,
                    "agent": q.agent_key,
                    "question_text": q.text,
                    "answer": q.answer,
                    "answered_at": _iso(q.answered_at),
                    "answered_by": q.answered_by,
                }
            )
        for m in briefs:
            at = _aware(m.created_at)
            if after and at and at < after:
                continue
            to = office.id if m.agent_key == "boss" else m.agent_key
            out.append(
                {
                    "id": f"brief-{m.id}",
                    "agent": "stef",
                    "to": to,
                    "question_text": f"Brief for {to}",
                    "answer": m.text,
                    "answered_at": _iso(m.created_at),
                    "answered_by": m.sent_by,
                }
            )
        out.sort(key=lambda r: r["answered_at"] or "")
        return out


class AckIn(BaseModel):
    ids: list[str] = Field(max_length=500)


async def _ack(office: Office, ids: list[str]) -> dict[str, Any]:
    now = datetime.now(UTC)
    brief_ids = [int(i[6:]) for i in ids if i.startswith("brief-") and i[6:].isdigit()]
    q_ids = [i for i in ids if not i.startswith("brief-")]
    acked = 0
    async with get_session_factory()() as db:
        if q_ids:
            for q in (
                await db.execute(
                    select(OfficeQuestion)
                    .where(OfficeQuestion.session_id == office.session_id)
                    .where(OfficeQuestion.qid.in_(q_ids))
                    .where(OfficeQuestion.status == "answered")
                )
            ).scalars():
                q.collected_at = now
                acked += 1
        if brief_ids:
            for m in (
                await db.execute(
                    select(OfficeMessage)
                    .where(OfficeMessage.session_id == office.session_id)
                    .where(OfficeMessage.id.in_(brief_ids))
                    .where(OfficeMessage.sender == "stef")
                )
            ).scalars():
                m.read_at = m.read_at or now
                acked += 1
        await db.commit()
    return {"status": "ok", "acked": acked}


@router.get("/onset/inbox")
async def onset_manager_inbox(
    since: str | None = None,
    x_onset_key: Annotated[str, Header()] = "",
    x_office_key: Annotated[str, Header()] = "",
) -> list[dict[str, Any]]:
    """onset: Stef's answers and briefs not yet collected. Ack them after reading."""
    office = _get_office("onset")
    _check_key(office.id, x_onset_key or x_office_key)
    return await _manager_inbox(office, since)


@router.post("/onset/inbox/ack")
async def onset_manager_ack(
    body: AckIn,
    x_onset_key: Annotated[str, Header()] = "",
    x_office_key: Annotated[str, Header()] = "",
) -> dict[str, Any]:
    office = _get_office("onset")
    _check_key(office.id, x_onset_key or x_office_key)
    return await _ack(office, body.ids)


@router.get("/office/{office_id}/inbox")
async def office_manager_inbox(
    office_id: str,
    since: str | None = None,
    x_office_key: Annotated[str, Header()] = "",
    x_onset_key: Annotated[str, Header()] = "",
) -> list[dict[str, Any]]:
    office = _get_office(office_id)
    _check_key(office.id, x_office_key or x_onset_key)
    return await _manager_inbox(office, since)


@router.post("/office/{office_id}/inbox/ack")
async def office_manager_ack(
    office_id: str,
    body: AckIn,
    x_office_key: Annotated[str, Header()] = "",
    x_onset_key: Annotated[str, Header()] = "",
) -> dict[str, Any]:
    office = _get_office(office_id)
    _check_key(office.id, x_office_key or x_onset_key)
    return await _ack(office, body.ids)
