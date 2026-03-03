from __future__ import annotations

import logging
from pathlib import Path

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()
templates: Jinja2Templates | None = None

_CONFIG_PATH = Path("config.yaml")
_ENV_PATH = Path(".env")


def set_templates(t: Jinja2Templates) -> None:
    global templates
    templates = t


def _load_yaml() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def _save_yaml(data: dict) -> None:
    with open(_CONFIG_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _load_env() -> dict[str, str]:
    """Return visible .env key/value pairs (never DISCORD_TOKEN)."""
    if not _ENV_PATH.exists():
        return {}
    from dotenv import dotenv_values
    raw = dict(dotenv_values(_ENV_PATH))
    return {k: v for k, v in raw.items() if k not in {"DISCORD_TOKEN", "DISCORD_GUILD_ID"} and v is not None}


def _mask(value: str) -> str:
    """Return a masked version of a secret for display."""
    if not value:
        return ""
    if len(value) <= 8:
        return "••••••••"
    return value[:4] + "••••" + value[-4:]


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, saved: str = ""):
    settings = get_settings()
    env = _load_env()
    return templates.TemplateResponse(request, "settings.html", {  # type: ignore[union-attr]
        "s": settings,
        "env": env,
        "mask": _mask,
        "title": request.app.state.dashboard_title,
        "saved": saved == "1",
    })


@router.post("/settings")
async def save_settings(request: Request):
    form = await request.form()

    def _str(name: str, default: str = "") -> str:
        v = form.get(name, default)
        return str(v).strip() if v is not None else default

    def _int(name: str, default: int = 0) -> int:
        try:
            return int(form.get(name, default))  # type: ignore[arg-type]
        except (ValueError, TypeError):
            return default

    def _float(name: str, default: float = 0.0) -> float:
        try:
            return float(form.get(name, default))  # type: ignore[arg-type]
        except (ValueError, TypeError):
            return default

    def _bool(name: str) -> bool:
        return form.get(name) == "on"

    raw = _load_yaml()

    def _set(section: str, key: str, value) -> None:
        if not isinstance(raw.get(section), dict):
            raw[section] = {}
        raw[section][key] = value

    # ── LLM ──────────────────────────────────────────────────────────────────
    _set("llm", "provider", _str("llm.provider", "ollama"))
    _set("llm", "ollama_base_url", _str("llm.ollama_base_url", "http://localhost:11434"))
    _set("llm", "ollama_model", _str("llm.ollama_model", "qwen3:8b"))
    _set("llm", "openai_model", _str("llm.openai_model", "gpt-4o-mini"))
    _set("llm", "temperature", _float("llm.temperature", 0.3))
    _set("llm", "max_tokens", _int("llm.max_tokens", 4096))
    _set("llm", "timeout_seconds", _int("llm.timeout_seconds", 360))
    _set("llm", "max_retries", _int("llm.max_retries", 2))

    # ── Whisper ───────────────────────────────────────────────────────────────
    _set("whisper", "model", _str("whisper.model", "base"))
    _set("whisper", "language", _str("whisper.language", "en"))

    # ── Search ────────────────────────────────────────────────────────────────
    _set("search", "enabled", _bool("search.enabled"))
    _set("search", "max_results", _int("search.max_results", 5))

    # ── Processing ────────────────────────────────────────────────────────────
    _set("processing", "max_concurrent_jobs", _int("processing.max_concurrent_jobs", 2))

    # ── Dashboard ─────────────────────────────────────────────────────────────
    dashboard_title = _str("dashboard.title", "Note Expander")
    _set("dashboard", "title", dashboard_title)

    # ── Integrations ──────────────────────────────────────────────────────────
    obsidian = _str("integrations.obsidian_vault_path", "")
    webhook = _str("integrations.webhook_url", "")
    _set("integrations", "obsidian_vault_path", obsidian or None)
    _set("integrations", "webhook_url", webhook or None)

    _save_yaml(raw)

    # ── .env secrets (blank = keep existing) ─────────────────────────────────
    for key in ("TAVILY_API_KEY", "OPENAI_API_KEY"):
        value = _str(key, "")
        if value:
            from dotenv import set_key
            _ENV_PATH.touch()
            set_key(str(_ENV_PATH), key, value)

    # ── Reload in-process settings ────────────────────────────────────────────
    get_settings.cache_clear()
    new_settings = get_settings()
    request.app.state.settings = new_settings
    request.app.state.dashboard_title = new_settings.dashboard.title

    logger.info("Settings saved and reloaded")
    return RedirectResponse("/settings?saved=1", status_code=303)
