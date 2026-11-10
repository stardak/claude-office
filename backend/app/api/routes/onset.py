"""Company offices adapter.

One small endpoint that lets Stef's bots drive an office with a single line of
JSON instead of Claude Code hook events. Each company gets its own office
(its own session in the left-hand list), team and key:

    POST /api/v1/office/{office}/status        e.g. /api/v1/office/hostmate/status
    X-Office-Key: <OFFICE_KEY_HOSTMATE>
    {"agent": "sales", "status": "working", "task": "Onboarding new vendor",
     "needs_approval": false, "handoff_to": null}

The legacy POST /api/v1/onset/status (X-Onset-Key: ONSET_OFFICE_KEY) still
drives the "onset" office.

Offices and teams come from OFFICES (fly.toml), one office per line or ";":

    onset:Onset = social:Social:📸, email:Email:✉️, ads:Ads:📣
    hostmate:Hostmate = sales:Sales:🤝, support:Support:🎧

The boss of each office is the office id ("onset", "hostmate"); staff sit in
listed order (max 7, the office has 8 desks). Keys are Fly secrets named
OFFICE_KEY_<ID>, so a leaked key for one company can't touch another office.

Only short task labels are accepted: anything that looks like an email
address, a link, money, a percentage or a long number is rejected, so customer
data, revenue and conversion figures, and secrets can't end up on screen.
"""

import hmac
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.api.routes.events import _check_rate_limit  # pyright: ignore[reportPrivateUsage]
from app.config import get_settings
from app.core.broadcast_service import broadcast_state
from app.core.event_processor import EventProcessor, get_event_processor
from app.models.common import BubbleContent, BubbleType
from app.models.events import EventAdapter

router = APIRouter()

STAFF_TYPE = "onset-staff"  # summary_service keeps these names verbatim
MAX_STAFF = 7  # 8 desks in the office
_ID = r"[a-z][a-z0-9-]{0,23}"
_ID_RE = re.compile(_ID)

_BLOCKED = [
    (re.compile(r"@"), "no email addresses or handles"),
    (re.compile(r"https?://|www\.|\.(com|co|io|net|org)\b", re.I), "no links"),
    (re.compile(r"[£$€¥]"), "no money figures"),
    (re.compile(r"\d{4,}"), "no long numbers"),
    (re.compile(r"%"), "no percentages or metrics"),
    (re.compile(r"\b(api[ _-]?keys?|tokens?|secrets?|passwords?)\b", re.I), "no secrets"),
]


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


class OfficeStatus(BaseModel):
    agent: str = Field(pattern=rf"^{_ID}$")
    status: Literal["working", "waiting", "idle", "done"] = "working"
    task: str = Field(default="", max_length=60)
    needs_approval: bool = False
    handoff_to: str | None = Field(default=None, pattern=rf"^{_ID}$")

    @field_validator("task")
    @classmethod
    def short_safe_label(cls, v: str) -> str:
        v = " ".join(v.split())
        for pattern, reason in _BLOCKED:
            if pattern.search(v):
                raise ValueError(f"task label rejected: {reason}")
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

    return {"status": "ok", "office": office.id, "session_id": office.session_id}


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
) -> dict[str, str]:
    office = _get_office(office_id)
    _check_key(office.id, x_office_key)
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
