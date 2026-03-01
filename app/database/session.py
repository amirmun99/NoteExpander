from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_db_url(db_path: str) -> str:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{db_path}"


async def init_db(db_path: str) -> None:
    global _engine, _session_factory

    url = _get_db_url(db_path)
    _engine = create_async_engine(url, echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight migration: add columns introduced after initial schema creation.
        result = await conn.execute(text("PRAGMA table_info(notes)"))
        existing_cols = {row[1] for row in result.fetchall()}
        if "requested_provider" not in existing_cols:
            await conn.execute(text("ALTER TABLE notes ADD COLUMN requested_provider TEXT"))


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _session_factory
