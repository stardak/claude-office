"""Onset office adapter.

One small endpoint that lets Stef's bots drive the office with a single line
of JSON instead of Claude Code hook events:

    POST /api/v1/onset/status
    X-Onset-Key: <ONSET_OFFICE_KEY>
    {"agent": "social", "status": "working", "task": "Drafting Wed carousel",
     "needs_approval": false, "handoff_to": null}

"onset" is the boss. "social", "email" and "ads" are the three staff desks.
Only short task labels are accepted: anything that looks like an email
address, a link, money or a long number is rejected, so customer data,
revenue figures and secrets can't end up on screen.
"""

import hmac
import re
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

SESSION_ID = "onset_office"
STAFF_TYPE = "onset-staff"  # summary_service keeps these names verbatim
STAFF: dict[str, tuple[str, str]] = {  # id -> (display name, icon)
    "social": ("Social", "📸"),
    "email": ("Email", "✉️"),
    "ads": ("Ads", "📣"),
}
BOSS_NAME = "onset"

_BLOCKED = [
    (re.compile(r"@"), "no email addresses or handles"),
    (re.compile(r"https?://|www\.|\.(com|co|io|net|org)\b", re.I), "no links"),
    (re.compile(r"[£$€¥]"), "no money figures"),
    (re.compile(r"\d{4,}"), "no long numbers"),
    (re.compile(r"(key|token|secret|password)", re.I), "no secrets"),
]


class OnsetStatus(BaseModel):
    agent: Literal["onset", "social", "email", "ads"]
    status: Literal["working", "waiting", "idle", "done"] = "working"
    task: str = Field(default="", max_length=60)
    needs_approval: bool = False
    handoff_to: Literal["onset", "social", "email", "ads"] | None = None

    @field_validator("task")
    @classmethod
    def short_safe_label(cls, v: str) -> str:
        v = " ".join(v.split())
        for pattern, reason in _BLOCKED:
            if pattern.search(v):
                raise ValueError(f"task label rejected: {reason}")
        return v


def _require_key(x_onset_key: Annotated[str, Header()] = "") -> None:
    expected = get_settings().ONSET_OFFICE_KEY
    if not expected:
        raise HTTPException(status_code=503, detail="Onset adapter disabled (no ONSET_OFFICE_KEY)")
    if not hmac.compare_digest(x_onset_key, expected):
        raise HTTPException(status_code=401, detail="Invalid X-Onset-Key")


async def _send(ep: EventProcessor, event_type: str, **data: Any) -> None:
    event = EventAdapter.validate_python(
        {
            "event_type": event_type,
            "session_id": SESSION_ID,
            "timestamp": datetime.now(UTC),
            "data": {"project_name": "Onset", **data},
        }
    )
    await ep.process_event(event)


async def _ensure_office(ep: EventProcessor) -> None:
    """Open the office and seat Social, Email, Ads (in that order) if missing."""
    await ep.get_current_state(SESSION_ID)  # restores from DB after a restart
    sm = ep.sessions.get(SESSION_ID)
    if sm is None or sm.phase.name in ("EMPTY", "ENDED"):
        await _send(ep, "session_start")
        sm = ep.sessions.get(SESSION_ID)
    seated: set[str] = set(sm.agents) if sm else set()
    for agent_id, (name, _icon) in STAFF.items():
        if agent_id not in seated:
            await _send(
                ep,
                "subagent_start",
                agent_id=agent_id,
                agent_name=name,
                agent_type=STAFF_TYPE,
                task_description=f"{name} desk",
            )


def _bubble(text: str, icon: str) -> BubbleContent:
    return BubbleContent(type=BubbleType.THOUGHT, text=text[:60], icon=icon)


@router.post("/onset/status", dependencies=[Depends(_require_key)])
async def onset_status(
    body: OnsetStatus,
    ep: Annotated[EventProcessor, Depends(get_event_processor)],
) -> dict[str, str]:
    _check_rate_limit(SESSION_ID)
    await _ensure_office(ep)
    task = body.task or body.status.capitalize()
    is_boss = body.agent == "onset"
    agent_id = None if is_boss else body.agent
    icon = "🧠" if is_boss else STAFF[body.agent][1]

    # 1. Drive the real state machine with repo events.
    if body.needs_approval:
        await _send(ep, "permission_request", agent_id=agent_id, tool_name=f"Approval: {task}")
        bubble = _bubble(f"Needs your OK: {task}", "🚨")
    elif body.status == "working":
        await _send(ep, "post_tool_use", agent_id=agent_id, tool_name=task)  # clears approval
        await _send(ep, "pre_tool_use", agent_id=agent_id, tool_name=task)
        bubble = _bubble(task, icon)
    else:
        await _send(ep, "post_tool_use", agent_id=agent_id, tool_name=task)
        label = {"waiting": "Waiting", "idle": "Idle", "done": "Done"}[body.status]
        bubble = _bubble(
            f"{label}: {body.task}" if body.task else label,
            {"waiting": "⏳", "idle": "☕", "done": "✅"}[body.status],
        )

    # 2. Cosmetic touches the hook events can't express (bubble, monitor, handoff).
    sm = ep.sessions.get(SESSION_ID)
    if sm is not None:
        if body.handoff_to and body.handoff_to != body.agent:
            to_name = BOSS_NAME if body.handoff_to == "onset" else STAFF[body.handoff_to][0]
            from_name = BOSS_NAME if is_boss else STAFF[body.agent][0]
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
        await broadcast_state(SESSION_ID, sm)

    return {"status": "ok", "session_id": SESSION_ID}
