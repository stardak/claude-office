"""Inline SQLite schema migration.

Moved out of ``app.main`` in ARC-023. The migration runs on startup against
the SQLite database to add columns introduced after the initial schema.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def migrate_schema(conn: AsyncConnection) -> None:
    """Add columns to existing tables that were added after initial schema.

    Only runs for SQLite. Uses ALTER TABLE ADD COLUMN which is a no-op if
    the column already exists (checked via PRAGMA first).

    NOTE: This project intentionally uses inline schema migration instead of
    Alembic.  The backend is SQLite-only and single-instance, so the lightweight
    PRAGMA-based approach is sufficient.  Alembic was removed as a dependency
    (see pyproject.toml).
    """
    dialect = conn.dialect.name
    if dialect != "sqlite":
        return

    new_columns: dict[str, str] = {
        "label": "TEXT DEFAULT NULL",
        "display_name": "TEXT DEFAULT NULL",
        "floor_id": "TEXT DEFAULT NULL",
        "room_id": "TEXT DEFAULT NULL",
        "team_name": "TEXT DEFAULT NULL",
        "teammate_name": "TEXT DEFAULT NULL",
        "is_lead": "BOOLEAN DEFAULT 0",
    }

    result = await conn.execute(text("PRAGMA table_info(sessions)"))
    existing = {row[1] for row in result.fetchall()}

    for col_name, col_def in new_columns.items():
        if col_name not in existing:
            await conn.execute(text(f"ALTER TABLE sessions ADD COLUMN {col_name} {col_def}"))

    # Office chat: who sent a brief (Cloudflare Access email).
    result = await conn.execute(text("PRAGMA table_info(office_message)"))
    msg_cols = {row[1] for row in result.fetchall()}
    if msg_cols and "sent_by" not in msg_cols:
        await conn.execute(text("ALTER TABLE office_message ADD COLUMN sent_by TEXT DEFAULT NULL"))
