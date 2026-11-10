from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class SessionRecord(Base):
    """Database model for Claude Code sessions."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_name: Mapped[str | None] = mapped_column(String, nullable=True)
    project_root: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    status: Mapped[str] = mapped_column(String, default="active")
    label: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    floor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    room_id: Mapped[str | None] = mapped_column(String, nullable=True)
    team_name: Mapped[str | None] = mapped_column(String, nullable=True)
    teammate_name: Mapped[str | None] = mapped_column(String, nullable=True)
    is_lead: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    events: Mapped[list[EventRecord]] = relationship(
        "EventRecord", back_populates="session", cascade="all, delete-orphan"
    )


class EventRecord(Base):
    """Database model for events within a session."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("sessions.id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_type: Mapped[str] = mapped_column(String)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)

    session: Mapped[SessionRecord] = relationship("SessionRecord", back_populates="events")


class TaskRecord(Base):
    """Database model for tasks within a session.

    Stores tasks from both the TodoWrite tool and the new task file system.
    Tasks are persisted to survive file system cleanup by Claude Code.
    """

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("sessions.id"), index=True)
    task_id: Mapped[str] = mapped_column(String)  # Original task ID (e.g., "1", "2")
    content: Mapped[str] = mapped_column(String)  # Subject/content of the task
    status: Mapped[str] = mapped_column(String)  # pending, in_progress, completed
    active_form: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    blocks: Mapped[str | None] = mapped_column(String, nullable=True)  # JSON-serialized list
    blocked_by: Mapped[str | None] = mapped_column(String, nullable=True)  # JSON-serialized list
    owner: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(String, nullable=True)  # JSON-serialized dict
    sort_order: Mapped[int] = mapped_column(default=0)  # For ordering tasks
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class UserPreference(Base):
    """Database model for user preferences.

    Stores key-value pairs for user preferences. Uses a flexible design
    to support adding new preferences without schema changes.
    """

    __tablename__ = "user_preferences"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class OfficeWork(Base):
    """Finished work an office agent handed in (email text, report, caption...).

    Written by the company offices adapter (POST /api/v1/office/{id}/status with
    ``detail`` and/or ``link``) and shown when Stef clicks that agent.
    """

    __tablename__ = "office_work"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, index=True)
    agent_key: Mapped[str] = mapped_column(String, index=True)  # agent id, or "boss"
    title: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    needs_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[str | None] = mapped_column(String, nullable=True)
    link: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class OfficeMessage(Base):
    """Chat between Stef and one office agent (briefs in, replies out).

    Stef writes from the office (agent popup); the bot collects unread messages
    from its inbox (GET /api/v1/office/{id}/inbox/{agent}) and answers via the
    ``reply`` field on its status update.
    """

    __tablename__ = "office_message"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, index=True)
    agent_key: Mapped[str] = mapped_column(String, index=True)  # agent id, or "boss"
    sender: Mapped[str] = mapped_column(String)  # "stef" or "bot"
    text: Mapped[str] = mapped_column(String)
    sent_by: Mapped[str | None] = mapped_column(String, nullable=True)  # Access email
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class OfficeQuestion(Base):
    """A question a bot asked Stef, and his answer (kept 90 days as the log).

    Posted with the ``question`` field on a status update; answered from the
    Work tab by Stef's Cloudflare Access login only; collected by the onset
    manager bot from GET /api/v1/onset/inbox and acknowledged.
    """

    __tablename__ = "office_question"
    __table_args__ = (UniqueConstraint("session_id", "qid", name="uq_office_question_qid"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, index=True)
    qid: Mapped[str] = mapped_column(String(64))
    agent_key: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(String)
    options: Mapped[list[str]] = mapped_column(JSON, default=list)
    allow_text: Mapped[bool] = mapped_column(Boolean, default=False)
    link: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="open")  # open|answered|closed
    answer: Mapped[str | None] = mapped_column(String, nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    answered_by: Mapped[str | None] = mapped_column(String, nullable=True)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
