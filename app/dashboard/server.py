from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from app.config import Settings

from .routes.analytics import router as analytics_router
from .routes.analytics import set_templates as analytics_set_templates
from .routes.export import router as export_router
from .routes.notes import router as notes_router, set_templates

TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_dashboard_app(settings: Settings) -> FastAPI:
    app = FastAPI(title=settings.dashboard.title, docs_url=None, redoc_url=None)

    # Store settings on app state
    app.state.dashboard_title = settings.dashboard.title
    app.state.settings = settings

    # Setup Jinja2 templates
    jinja_templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    set_templates(jinja_templates)
    analytics_set_templates(jinja_templates)

    # Register routers
    app.include_router(notes_router)
    app.include_router(export_router)
    app.include_router(analytics_router)

    return app
