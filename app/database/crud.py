from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AgentLog, Note, ReportVersion, SearchResult
from .session import get_session_factory


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_note(
    *,
    raw_text: str,
    source: str,
    discord_user_id: str,
    discord_channel_id: str,
    discord_message_id: str,
    requested_provider: Optional[str] = None,
) -> Note:
    factory = get_session_factory()
    async with factory() as session:
        note = Note(
            raw_text=raw_text,
            source=source,
            discord_user_id=discord_user_id,
            discord_channel_id=discord_channel_id,
            discord_message_id=discord_message_id,
            status="pending",
            requested_provider=requested_provider,
        )
        session.add(note)
        await session.commit()
        await session.refresh(note)
        return note


async def get_note(note_id: str) -> Optional[Note]:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(Note).where(Note.id == note_id))
        return result.scalar_one_or_none()


async def update_note_status(
    note_id: str,
    status: str,
    error_message: Optional[str] = None,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(Note).where(Note.id == note_id))
        note = result.scalar_one_or_none()
        if note is None:
            return
        note.status = status
        if status == "processing":
            note.processing_started_at = _now()
        elif status in ("complete", "failed"):
            note.processing_finished_at = _now()
        if error_message is not None:
            note.error_message = error_message
        await session.commit()


async def save_report(
    note_id: str,
    *,
    report_markdown: str,
    report_title: str,
    project_type: str,
    confidence: float,
    llm_provider: str,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(Note).where(Note.id == note_id))
        note = result.scalar_one_or_none()
        if note is None:
            return
        note.report_markdown = report_markdown
        note.report_title = report_title
        note.project_type = project_type
        note.confidence = confidence
        note.llm_provider = llm_provider
        await session.commit()


async def save_search_results(
    note_id: str,
    results: list[dict],
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        for r in results:
            sr = SearchResult(
                note_id=note_id,
                query=r.get("query"),
                title=r.get("title"),
                url=r.get("url"),
                snippet=r.get("snippet") or r.get("content"),
                score=r.get("score"),
            )
            session.add(sr)
        await session.commit()


async def save_agent_log(
    note_id: str,
    *,
    agent_name: str,
    task_name: str,
    input_text: str,
    output_text: str,
    duration_seconds: float,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        log = AgentLog(
            note_id=note_id,
            agent_name=agent_name,
            task_name=task_name,
            input_text=input_text,
            output_text=output_text,
            duration_seconds=duration_seconds,
        )
        session.add(log)
        await session.commit()


_SORT_COLUMNS = {
    "date": Note.created_at,
    "confidence": Note.confidence,
    "type": Note.project_type,
}

PAGE_SIZE = 25


async def list_notes(limit: int = 100, offset: int = 0, sort: str = "date") -> list[Note]:
    col = _SORT_COLUMNS.get(sort, Note.created_at)
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(Note).order_by(col.desc()).offset(offset).limit(limit)
        )
        return list(result.scalars().all())


async def count_notes() -> int:
    """Total note count for pagination (3d)."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(func.count()).select_from(Note))
        return result.scalar_one()


async def search_notes(q: str = "", type_filter: str = "", offset: int = 0, sort: str = "date") -> list[Note]:
    """Search notes by text (title or raw_text) and/or project type (C1, 3d)."""
    col = _SORT_COLUMNS.get(sort, Note.created_at)
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(Note).order_by(col.desc()).offset(offset).limit(PAGE_SIZE + 1)
        if type_filter and type_filter != "all":
            stmt = stmt.where(Note.project_type == type_filter)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                or_(
                    Note.report_title.ilike(like),
                    Note.raw_text.ilike(like),
                )
            )
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def delete_note(note_id: str) -> None:
    """Permanently delete a note and its related records (C2). Cascade handles FK children."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(Note).where(Note.id == note_id))
        note = result.scalar_one_or_none()
        if note is not None:
            await session.delete(note)
            await session.commit()


async def reset_note_for_rerun(note_id: str) -> bool:
    """
    Reset a note so it can be reprocessed (C3).
    Saves current report as a version snapshot before clearing (3c).
    Clears report, error, logs, search results, resets status to pending.
    Returns True if the note was found and reset, False otherwise.
    """
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(select(Note).where(Note.id == note_id))
        note = result.scalar_one_or_none()
        if note is None:
            return False

        # Snapshot existing report as a version before clearing (3c)
        if note.report_markdown:
            version = ReportVersion(
                note_id=note_id,
                report_markdown=note.report_markdown,
                report_title=note.report_title,
                llm_provider=note.llm_provider,
            )
            session.add(version)

        # Clear child records
        await session.execute(delete(AgentLog).where(AgentLog.note_id == note_id))
        await session.execute(delete(SearchResult).where(SearchResult.note_id == note_id))

        # Reset note fields
        note.status = "pending"
        note.report_markdown = None
        note.report_title = None
        note.project_type = None
        note.confidence = None
        note.error_message = None
        note.llm_provider = None
        note.processing_started_at = None
        note.processing_finished_at = None

        await session.commit()
        return True


async def get_report_versions(note_id: str) -> list[ReportVersion]:
    """Fetch all previous report versions for a note, newest first (3c)."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(ReportVersion)
            .where(ReportVersion.note_id == note_id)
            .order_by(ReportVersion.id.desc())
        )
        return list(result.scalars().all())


async def get_note_logs(note_id: str) -> list[AgentLog]:
    """Fetch agent logs for a note, ordered by id (insertion order) (C4)."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(AgentLog)
            .where(AgentLog.note_id == note_id)
            .order_by(AgentLog.id)
        )
        return list(result.scalars().all())


async def get_note_sources(note_id: str) -> list[SearchResult]:
    """Fetch search results (sources) for a note, ordered by score desc (3b)."""
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            select(SearchResult)
            .where(SearchResult.note_id == note_id)
            .order_by(SearchResult.score.desc())
        )
        return list(result.scalars().all())


async def get_analytics_stats() -> dict:
    """Aggregate stats for the analytics page (3a)."""
    from datetime import timedelta

    factory = get_session_factory()
    async with factory() as session:
        # Notes by type
        type_rows = await session.execute(
            select(Note.project_type, func.count().label("n"))
            .where(Note.project_type.isnot(None))
            .group_by(Note.project_type)
            .order_by(func.count().desc())
        )
        by_type = {row[0]: row[1] for row in type_rows}

        # Notes by status
        status_rows = await session.execute(
            select(Note.status, func.count().label("n"))
            .group_by(Note.status)
        )
        by_status = {row[0]: row[1] for row in status_rows}

        # Average processing time (seconds) for complete notes
        avg_rows = await session.execute(
            select(
                Note.llm_provider,
                func.avg(
                    func.julianday(Note.processing_finished_at) -
                    func.julianday(Note.processing_started_at)
                ).label("avg_days")
            )
            .where(Note.status == "complete")
            .where(Note.processing_started_at.isnot(None))
            .where(Note.processing_finished_at.isnot(None))
            .group_by(Note.llm_provider)
        )
        avg_time_by_provider = {
            row[0] or "unknown": round((row[1] or 0) * 86400, 1)
            for row in avg_rows
        }

        # Notes per day (last 30 days) — using SQLite date()
        daily_rows = await session.execute(
            select(
                func.date(Note.created_at).label("day"),
                func.count().label("n"),
            )
            .where(Note.created_at >= (
                # last 30 days: we compute this in Python
                func.datetime("now", "-30 days")
            ))
            .group_by(func.date(Note.created_at))
            .order_by(func.date(Note.created_at))
        )
        notes_per_day = {row[0]: row[1] for row in daily_rows}

        # Top search queries (most frequent)
        query_rows = await session.execute(
            select(SearchResult.query, func.count().label("n"))
            .where(SearchResult.query.isnot(None))
            .group_by(SearchResult.query)
            .order_by(func.count().desc())
            .limit(10)
        )
        top_queries = [(row[0], row[1]) for row in query_rows]

        # Provider breakdown
        provider_rows = await session.execute(
            select(Note.requested_provider, func.count().label("n"))
            .where(Note.status == "complete")
            .group_by(Note.requested_provider)
        )
        by_provider = {}
        for row in provider_rows:
            key = "openai (#deep)" if row[0] == "openai" else "default"
            by_provider[key] = row[1]

        total = sum(by_status.values())

        return {
            "total": total,
            "by_type": by_type,
            "by_status": by_status,
            "avg_time_by_provider": avg_time_by_provider,
            "notes_per_day": notes_per_day,
            "top_queries": top_queries,
            "by_provider": by_provider,
        }
