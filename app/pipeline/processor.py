from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

from app.agents.crew import build_and_run_crew
from app.config import Settings
from app.database.crud import (
    get_note,
    save_agent_log,
    save_report,
    save_search_results,
    update_note_status,
)
from app.pipeline import progress as prog

if TYPE_CHECKING:
    import discord

logger = logging.getLogger(__name__)

# Semaphore enforcing max_concurrent_jobs (B1).
# Initialised lazily on first call so it's created inside the running event loop.
_semaphore: asyncio.Semaphore | None = None


def _get_semaphore(max_jobs: int) -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max_jobs)
    return _semaphore


async def process_note(
    note_id: str,
    settings: Settings,
    bot: Optional["discord.Client"] = None,
    pipeline_flags: Optional[dict] = None,
) -> None:
    """
    Full async pipeline for a single note:
    1. Wait for a concurrency slot (B1 — honours max_concurrent_jobs)
    2. Mark as processing
    3. Run pipeline in thread (non-blocking)
    4. Save results
    5. Mark complete or failed
    6. Notify via Discord
    """
    semaphore = _get_semaphore(settings.processing.max_concurrent_jobs)

    async with semaphore:
        logger.info("Processing note %s", note_id)

        try:
            await update_note_status(note_id, "processing")

            note = await get_note(note_id)
            if note is None:
                logger.error("Note %s not found in DB", note_id)
                return

            # Resolve model override for #deep tag (B2: pass api_key directly, no os.environ write)
            model_override: Optional[str] = None
            api_key: Optional[str] = None
            effective_provider_label = settings.llm_provider_label

            if note.requested_provider == "openai":
                if settings.openai_api_key:
                    api_key = settings.openai_api_key
                    model_override = settings.llm.openai_model
                    effective_provider_label = f"openai/{settings.llm.openai_model}"
                    logger.info("Note %s: using OpenAI override (%s)", note_id, model_override)
                else:
                    logger.warning(
                        "Note %s requested OpenAI (#deep) but OPENAI_API_KEY is not set — using default",
                        note_id,
                    )

            # Initialise progress tracker, then run the blocking pipeline in a thread pool
            prog.init(note_id)
            crew_result = await asyncio.to_thread(
                build_and_run_crew,
                note.raw_text,
                settings,
                note_id,
                model_override,
                api_key,
                pipeline_flags or {},
            )

            classification = crew_result.classification
            project_type = classification.get("type", "unknown")
            confidence = float(classification.get("confidence", 0.5))
            title = classification.get("title") or "Untitled Note"

            await save_report(
                note_id,
                report_markdown=crew_result.raw_output,
                report_title=title,
                project_type=project_type,
                confidence=confidence,
                llm_provider=effective_provider_label,
            )

            if crew_result.search_results:
                await save_search_results(note_id, crew_result.search_results)

            for log in crew_result.agent_logs:
                await save_agent_log(
                    note_id,
                    agent_name=log["agent_name"],
                    task_name=log["task_name"],
                    input_text=log["input_text"],
                    output_text=log["output_text"],
                    duration_seconds=log["duration_seconds"],
                )

            await update_note_status(note_id, "complete")
            logger.info("Note %s completed successfully (type=%s)", note_id, project_type)

            # Obsidian vault sync (4c)
            if settings.integrations.obsidian_vault_path:
                source_urls = [
                    r["url"] for r in crew_result.search_results if r.get("url")
                ]
                await asyncio.to_thread(
                    _sync_obsidian,
                    settings.integrations.obsidian_vault_path,
                    note_id, title, project_type, confidence,
                    crew_result.raw_output,
                    effective_provider_label,
                    note.created_at,
                    source_urls,
                )

            # Webhook notification (4b)
            if settings.integrations.webhook_url:
                dashboard_host = settings.dashboard.host
                if dashboard_host == "0.0.0.0":
                    dashboard_host = "localhost"
                note_url = f"http://{dashboard_host}:{settings.dashboard.port}/notes/{note_id}"
                await _send_webhook(
                    settings.integrations.webhook_url,
                    note_id=note_id,
                    title=title,
                    project_type=project_type,
                    confidence=confidence,
                    note_url=note_url,
                )

            if bot is not None:
                await _notify_complete(
                    bot, note, title, project_type, confidence,
                    crew_result.classification.get("key_themes", []),
                    settings,
                )

        except Exception as e:
            logger.exception("Note %s processing failed: %s", note_id, e)
            await update_note_status(note_id, "failed", error_message=str(e))

            if bot is not None:
                try:
                    note = await get_note(note_id)
                    if note:
                        await _notify_failed(bot, note, str(e))
                except Exception:
                    logger.exception("Failed to send failure notification for note %s", note_id)


async def _notify_complete(
    bot: "discord.Client",
    note,
    title: str,
    project_type: str,
    confidence: float,
    key_themes: list,
    settings: Settings,
) -> None:
    try:
        import discord as _discord

        channel = bot.get_channel(int(note.discord_channel_id))
        if channel is None:
            channel = await bot.fetch_channel(int(note.discord_channel_id))

        dashboard_host = settings.dashboard.host
        if dashboard_host == "0.0.0.0":
            dashboard_host = "localhost"
        url = f"http://{dashboard_host}:{settings.dashboard.port}/notes/{note.id}"

        TYPE_COLOURS = {
            "software": _discord.Colour.blue(),
            "hardware": _discord.Colour.orange(),
            "mechanical": _discord.Colour.from_rgb(180, 130, 70),
            "business": _discord.Colour.green(),
        }
        colour = TYPE_COLOURS.get(project_type, _discord.Colour.blurple())

        embed = _discord.Embed(
            title=title,
            url=url,
            colour=colour,
        )
        embed.add_field(name="Type", value=project_type.capitalize(), inline=True)
        embed.add_field(name="Confidence", value=f"{confidence * 100:.0f}%", inline=True)

        if key_themes:
            embed.add_field(
                name="Key themes",
                value=" · ".join(str(t) for t in key_themes[:6]),
                inline=False,
            )

        embed.add_field(
            name="Links",
            value=f"[View report]({url}) · [Export .md]({url}/export.md)",
            inline=False,
        )
        embed.set_footer(text=f"via {note.llm_provider or settings.llm_provider_label}")

        await channel.send(
            content=f"<@{note.discord_user_id}> Research complete!",
            embed=embed,
        )
    except Exception:
        logger.exception("Failed to send completion notification")


def _sync_obsidian(
    vault_path: str,
    note_id: str,
    title: str,
    project_type: str,
    confidence: float,
    report_markdown: str,
    llm_provider: str,
    created_at,
    source_urls: list[str],
) -> None:
    """Blocking helper — runs in thread (4c)."""
    from app.integrations.obsidian import sync_to_obsidian
    sync_to_obsidian(
        vault_path=vault_path,
        note_id=note_id,
        title=title,
        project_type=project_type,
        confidence=confidence,
        report_markdown=report_markdown,
        llm_provider=llm_provider,
        created_at=created_at,
        source_urls=source_urls,
    )


async def _send_webhook(
    webhook_url: str,
    *,
    note_id: str,
    title: str,
    project_type: str,
    confidence: float,
    note_url: str,
) -> None:
    """POST completion payload to configured webhook URL (4b)."""
    try:
        import httpx
        payload = {
            "note_id": note_id,
            "title": title,
            "type": project_type,
            "confidence": round(confidence, 3),
            "url": note_url,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=payload)
            resp.raise_for_status()
            logger.info("Webhook sent for note %s → %d", note_id, resp.status_code)
    except Exception as e:
        logger.warning("Webhook failed for note %s: %s", note_id, e)


async def _notify_failed(bot: "discord.Client", note, error: str) -> None:
    try:
        channel = bot.get_channel(int(note.discord_channel_id))
        if channel is None:
            channel = await bot.fetch_channel(int(note.discord_channel_id))
        await channel.send(
            f"<@{note.discord_user_id}> Sorry, research failed for your note. "
            f"Error: `{error[:200]}`"
        )
    except Exception:
        logger.exception("Failed to send failure notification")
