from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    raw_text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(10))  # "text" | "voice"
    discord_user_id: Mapped[str] = mapped_column(String(32))
    discord_channel_id: Mapped[str] = mapped_column(String(32))
    discord_message_id: Mapped[str] = mapped_column(String(32), unique=True)
    project_type: Mapped[Optional[str]] = mapped_column(String(20))  # software|hardware|mechanical|business|unknown
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending|processing|complete|failed
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    report_markdown: Mapped[Optional[str]] = mapped_column(Text)
    report_title: Mapped[Optional[str]] = mapped_column(String(500))
    processing_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    processing_finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    llm_provider: Mapped[Optional[str]] = mapped_column(String(100))
    requested_provider: Mapped[Optional[str]] = mapped_column(String(50))  # "openai" when #deep tag used

    search_results: Mapped[list[SearchResult]] = relationship(
        "SearchResult", back_populates="note", cascade="all, delete-orphan"
    )
    agent_logs: Mapped[list[AgentLog]] = relationship(
        "AgentLog", back_populates="note", cascade="all, delete-orphan"
    )
    report_versions: Mapped[list["ReportVersion"]] = relationship(
        "ReportVersion", back_populates="note", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Note id={self.id!r} status={self.status!r}>"


class SearchResult(Base):
    __tablename__ = "search_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    note_id: Mapped[str] = mapped_column(String(36), ForeignKey("notes.id", ondelete="CASCADE"), index=True)
    query: Mapped[Optional[str]] = mapped_column(Text)
    title: Mapped[Optional[str]] = mapped_column(Text)
    url: Mapped[Optional[str]] = mapped_column(Text)
    snippet: Mapped[Optional[str]] = mapped_column(Text)
    score: Mapped[Optional[float]] = mapped_column(Float)

    note: Mapped[Note] = relationship("Note", back_populates="search_results")


class AgentLog(Base):
    __tablename__ = "agent_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    note_id: Mapped[str] = mapped_column(String(36), ForeignKey("notes.id", ondelete="CASCADE"), index=True)
    agent_name: Mapped[Optional[str]] = mapped_column(String(100))
    task_name: Mapped[Optional[str]] = mapped_column(String(100))
    input_text: Mapped[Optional[str]] = mapped_column(Text)
    output_text: Mapped[Optional[str]] = mapped_column(Text)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float)

    note: Mapped[Note] = relationship("Note", back_populates="agent_logs")


class ReportVersion(Base):
    """Snapshot of a report before it is overwritten by a re-run (3c)."""

    __tablename__ = "report_versions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    note_id: Mapped[str] = mapped_column(String(36), ForeignKey("notes.id", ondelete="CASCADE"), index=True)
    report_markdown: Mapped[str] = mapped_column(Text)
    report_title: Mapped[Optional[str]] = mapped_column(String(500))
    llm_provider: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    note: Mapped[Note] = relationship("Note", back_populates="report_versions")
