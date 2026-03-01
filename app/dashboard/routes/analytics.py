from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.database.crud import get_analytics_stats

logger = logging.getLogger(__name__)

router = APIRouter()
templates: Jinja2Templates | None = None


def set_templates(t: Jinja2Templates) -> None:
    global templates
    templates = t


@router.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    stats = await get_analytics_stats()
    return templates.TemplateResponse(request, "analytics.html", {  # type: ignore[union-attr]
        "stats": stats,
        "title": request.app.state.dashboard_title,
    })
