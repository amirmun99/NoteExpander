from __future__ import annotations

import logging

import bleach
import markdown as md
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from app.database.crud import (
    PAGE_SIZE,
    count_notes,
    delete_note,
    get_note,
    get_note_logs,
    get_note_sources,
    get_report_versions,
    list_notes,
    reset_note_for_rerun,
    search_notes,
)
from app.pipeline import progress as prog

logger = logging.getLogger(__name__)

router = APIRouter()
templates: Jinja2Templates | None = None

# Tags and attributes allowed after markdown → HTML conversion (B4)
_ALLOWED_TAGS = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "ul", "ol", "li",
    "strong", "em", "del", "code", "pre",
    "blockquote",
    "table", "thead", "tbody", "tr", "th", "td",
    "a", "img",
    "div", "span",
]
_ALLOWED_ATTRS: dict = {
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title"],
    "th": ["align"],
    "td": ["align"],
    "*": ["class"],
}


def _sanitize_html(html: str) -> str:
    return bleach.clean(html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)


def set_templates(t: Jinja2Templates) -> None:
    global templates
    templates = t


def _render(request: Request, template: str, context: dict) -> HTMLResponse:
    assert templates is not None
    return templates.TemplateResponse(request, template, context)


def _fmt_dt(dt) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _render_markdown(text: str) -> str:
    raw_html = md.markdown(text, extensions=["tables", "fenced_code", "nl2br"])
    return _sanitize_html(raw_html)


# ── Notes list (C1: search + type filter) ────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def notes_list(request: Request, q: str = "", type: str = "", sort: str = "date", page: int = 1):
    page = max(1, page)
    offset = (page - 1) * PAGE_SIZE

    if q or (type and type != "all"):
        notes_page = await search_notes(q=q, type_filter=type, offset=offset, sort=sort)
        # search_notes fetches PAGE_SIZE+1 to detect next page
        has_next = len(notes_page) > PAGE_SIZE
        notes = notes_page[:PAGE_SIZE]
        total_count = None  # not computed for filtered queries
    else:
        total_count = await count_notes()
        notes = await list_notes(limit=PAGE_SIZE, offset=offset, sort=sort)
        has_next = (offset + PAGE_SIZE) < total_count

    return _render(request, "index.html", {
        "notes": notes,
        "fmt_dt": _fmt_dt,
        "title": request.app.state.dashboard_title,
        "search_q": q,
        "search_type": type,
        "sort": sort,
        "page": page,
        "has_next": has_next,
        "has_prev": page > 1,
        "page_size": PAGE_SIZE,
        "total_count": total_count,
    })


# ── Note detail (C4: agent logs included) ────────────────────────────────────

@router.get("/notes/{note_id}", response_class=HTMLResponse)
async def note_detail(request: Request, note_id: str):
    note = await get_note(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")

    rendered_html = ""
    if note.report_markdown:
        rendered_html = _render_markdown(note.report_markdown)

    agent_logs = []
    sources = []
    report_versions = []
    if note.status == "complete":
        agent_logs = await get_note_logs(note_id)
        sources = await get_note_sources(note_id)
        report_versions = await get_report_versions(note_id)

    return _render(request, "note_detail.html", {
        "note": note,
        "rendered_html": rendered_html,
        "agent_logs": agent_logs,
        "sources": sources,
        "report_versions": report_versions,
        "fmt_dt": _fmt_dt,
        "title": request.app.state.dashboard_title,
        "render_markdown": _render_markdown,
    })


# ── Live progress fragment ────────────────────────────────────────────────────

@router.get("/notes/{note_id}/progress", response_class=HTMLResponse)
async def note_progress(request: Request, note_id: str):
    note = await get_note(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    stages = prog.get(note_id)
    return _render(request, "_progress.html", {
        "note": note,
        "stages": stages,
        "stage_order": prog.STAGE_ORDER,
        "stage_labels": prog.STAGE_LABELS,
    })


# ── Status JSON (kept for compatibility) ─────────────────────────────────────

@router.get("/notes/{note_id}/status")
async def note_status(note_id: str):
    note = await get_note(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")

    updated_at = note.processing_finished_at or note.processing_started_at or note.created_at
    return {
        "status": note.status,
        "updated_at": _fmt_dt(updated_at),
    }


# ── Delete note (C2) ─────────────────────────────────────────────────────────

@router.delete("/notes/{note_id}")
async def delete_note_route(note_id: str):
    note = await get_note(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    await delete_note(note_id)
    # HTMX reads HX-Redirect and navigates the browser to /
    return Response(status_code=200, headers={"HX-Redirect": "/"})


# ── Re-run note (C3) ─────────────────────────────────────────────────────────

@router.post("/notes/{note_id}/rerun")
async def rerun_note(request: Request, note_id: str):
    from app.config import get_settings
    from app.pipeline.processor import process_note
    import asyncio

    note = await get_note(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")

    if note.status in ("pending", "processing"):
        raise HTTPException(status_code=409, detail="Note is already processing")

    ok = await reset_note_for_rerun(note_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to reset note")

    settings = get_settings()
    asyncio.create_task(
        process_note(note_id, settings),
        name=f"process_note_{note_id}",
    )

    # HTMX will reload the page
    return Response(status_code=200, headers={"HX-Redirect": f"/notes/{note_id}"})
