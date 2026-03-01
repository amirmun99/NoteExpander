"""
Note Expander — Entry Point
Runs the Discord bot and local web dashboard concurrently.
"""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import sys
from pathlib import Path


def _setup_logging(level: str, log_file: str) -> None:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    file_handler.setFormatter(fmt)
    handlers.append(file_handler)

    for h in handlers:
        h.setFormatter(fmt)

    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), handlers=handlers)

    # Quieten noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


async def main() -> None:
    from app.config import get_settings
    from app.database.session import init_db
    from app.discord_bot.bot import NoteExpanderBot
    from app.discord_bot.state import BotState
    from app.dashboard.server import create_dashboard_app

    settings = get_settings()

    _setup_logging(settings.logging.level, settings.logging.file)
    logger = logging.getLogger(__name__)

    # Validate critical secrets
    if not settings.discord_token:
        logger.error("DISCORD_TOKEN is not set. Copy .env.example → .env and fill in your token.")
        sys.exit(1)

    if not settings.tavily_api_key and settings.search.enabled:
        logger.warning(
            "TAVILY_API_KEY not set — web search will be disabled. "
            "Set it in .env or disable search in config.yaml."
        )

    # Ensure data dirs exist
    Path(settings.processing.audio_download_dir).mkdir(parents=True, exist_ok=True)

    # Initialise database
    await init_db(settings.processing.db_path)
    logger.info("Database ready at %s", settings.processing.db_path)

    # Load persistent bot state (listen channels, dynamic user list)
    state = BotState.load("data/bot_state.json")

    # Build Discord bot and FastAPI dashboard
    bot = NoteExpanderBot(settings, state)
    app = create_dashboard_app(settings)

    # Configure uvicorn server
    import uvicorn
    config = uvicorn.Config(
        app,
        host=settings.dashboard.host,
        port=settings.dashboard.port,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    display_host = settings.dashboard.host if settings.dashboard.host != "0.0.0.0" else "localhost"
    logger.info(
        "Starting Note Expander — Dashboard: http://%s:%d | LLM: %s",
        display_host,
        settings.dashboard.port,
        settings.llm_model_string,
    )

    # Run both concurrently, with graceful shutdown on cancellation (B3)
    bot_task = asyncio.create_task(bot.start(settings.discord_token), name="discord_bot")
    server_task = asyncio.create_task(server.serve(), name="uvicorn_server")

    try:
        await asyncio.gather(bot_task, server_task)
    except asyncio.CancelledError:
        logger.info("Shutdown signal received — stopping services...")
    finally:
        # Give in-flight pipeline tasks up to 30s to finish
        pending = [
            t for t in asyncio.all_tasks()
            if t.get_name().startswith("process_note_") and not t.done()
        ]
        if pending:
            logger.info("Waiting for %d in-flight pipeline task(s) to finish (max 30s)…", len(pending))
            done, still_running = await asyncio.wait(pending, timeout=30)
            for t in still_running:
                t.cancel()

        if not bot.is_closed():
            await bot.close()
        server.should_exit = True


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Note Expander] Shutting down.")
