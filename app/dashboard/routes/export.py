from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from app.database.crud import get_note

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/notes/{note_id}/export.md", response_class=PlainTextResponse)
async def export_note_markdown(note_id: str):
    note = await get_note(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")

    if not note.report_markdown:
        raise HTTPException(status_code=404, detail="Report not yet available")

    filename = f"note-{note_id[:8]}.md"
    return PlainTextResponse(
        content=note.report_markdown,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
