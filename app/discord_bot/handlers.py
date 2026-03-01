from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import discord

from app.config import Settings
from app.database.crud import create_note
from app.pipeline.processor import process_note

from .state import BotState
from .transcription import transcribe_attachment
from .url_fetcher import enrich_text_with_urls, find_urls

logger = logging.getLogger(__name__)

_VOICE_MIME_TYPES = {"audio/ogg", "audio/mpeg", "audio/mp4", "audio/webm", "audio/wav"}
_VOICE_EXTENSIONS = {".ogg", ".mp3", ".mp4", ".m4a", ".webm", ".wav", ".oga"}


def _is_voice_attachment(attachment: discord.Attachment) -> bool:
    if attachment.content_type and any(
        attachment.content_type.startswith(mt) for mt in _VOICE_MIME_TYPES
    ):
        return True
    return Path(attachment.filename).suffix.lower() in _VOICE_EXTENSIONS


def _strip_mention(content: str, bot_id: int) -> str:
    """Remove @bot mention(s) from message content."""
    return re.sub(rf"<@!?{bot_id}>", "", content).strip()


def _extract_tags(text: str) -> tuple[str, str | None, dict]:
    """
    Extract special tags from the text (1b).

    Tags:
      #deep     → use OpenAI instead of local Ollama
      #quick    → skip web search (offline mode, faster)
      #market   → force project_type to "business"
      #tech     → force project_type to "software"
      #noformat → skip formatter stage, return analyst output directly

    Returns (cleaned_text, requested_provider_or_None, flags_dict).
    flags_dict keys: skip_search, force_type, no_format.
    """
    requested_provider = None
    flags: dict = {}

    if re.search(r"#deep\b", text, re.IGNORECASE):
        requested_provider = "openai"
        text = re.sub(r"#deep\b", "", text, flags=re.IGNORECASE)

    if re.search(r"#quick\b", text, re.IGNORECASE):
        flags["skip_search"] = True
        text = re.sub(r"#quick\b", "", text, flags=re.IGNORECASE)

    if re.search(r"#market\b", text, re.IGNORECASE):
        flags["force_type"] = "business"
        text = re.sub(r"#market\b", "", text, flags=re.IGNORECASE)
    elif re.search(r"#tech\b", text, re.IGNORECASE):
        flags["force_type"] = "software"
        text = re.sub(r"#tech\b", "", text, flags=re.IGNORECASE)

    if re.search(r"#noformat\b", text, re.IGNORECASE):
        flags["no_format"] = True
        text = re.sub(r"#noformat\b", "", text, flags=re.IGNORECASE)

    return text.strip(), requested_provider, flags


async def handle_message(
    message: discord.Message,
    settings: Settings,
    state: BotState,
    bot: discord.Client,
    is_mentioned: bool = False,
) -> None:
    """Route a Discord message to the text or voice research pipeline."""
    if not state.is_allowed(message.author.id, settings.discord.allowed_user_ids):
        logger.debug("Ignoring message from unauthorized user %s", message.author.id)
        return

    source = "text"
    raw_text = ""
    voice_attachment = None

    for attachment in message.attachments:
        if _is_voice_attachment(attachment):
            voice_attachment = attachment
            source = "voice"
            break

    requested_provider: str | None = None

    if source == "voice" and voice_attachment is not None:
        ack = await message.reply("Got your voice note! Transcribing…")
        try:
            raw_text = await transcribe_attachment(
                voice_attachment,
                audio_dir=settings.processing.audio_download_dir,
                whisper_model_name=settings.whisper.model,
                language=settings.whisper.language,
            )
        except Exception as e:
            logger.exception("Transcription failed: %s", e)
            await ack.edit(content=f"Transcription failed: `{e}`")
            return

        if not raw_text.strip():
            await ack.edit(content="Could not transcribe audio — please try again or send as text.")
            return

        raw_text, requested_provider, flags = _extract_tags(raw_text)
        preview = raw_text[:200] + ("…" if len(raw_text) > 200 else "")
        model_label = "OpenAI" if requested_provider == "openai" else (
            "Ollama" if settings.llm.provider == "ollama" else "OpenAI"
        )
        await ack.edit(content=f"Transcribed: *\"{preview}\"*\nResearching now ({model_label})…")

    else:
        # Strip bot mention if this was an @mention invocation
        content = message.content
        if is_mentioned and bot.user:
            content = _strip_mention(content, bot.user.id)

        raw_text, requested_provider, flags = _extract_tags(content.strip())

        if not raw_text:
            if is_mentioned:
                await message.reply(
                    "Hi! Mention me with your idea and I'll research it.\n"
                    "Try: `@me Build a Raspberry Pi weather station`\n"
                    "Tags: `#deep` (OpenAI), `#quick` (no search), `#market`/`#tech` (force type), `#noformat` (raw output)\n"
                    "Or paste a URL and I'll fetch and analyse its content.\n"
                    "Or use `/research <idea>` — or just DM me."
                )
            return

        if requested_provider == "openai":
            model_label = f"OpenAI · `{settings.llm.openai_model}`"
        else:
            model_label = f"{'Ollama' if settings.llm.provider == 'ollama' else 'OpenAI'} · `{settings.llm_model_string}`"

        # Detect URLs and fetch their content (1a)
        urls_in_text = find_urls(raw_text)
        fetched_urls: list[str] = []
        if urls_in_text:
            ack = await message.reply(
                f"Got it! Fetching {len(urls_in_text)} URL(s) then researching ({model_label})…"
            )
            raw_text, fetched_urls = await enrich_text_with_urls(raw_text)
            if fetched_urls:
                await ack.edit(
                    content=(
                        f"Fetched content from {len(fetched_urls)} URL(s). "
                        f"Researching now ({model_label})…"
                    )
                )
            else:
                await ack.edit(
                    content=f"Could not fetch URLs (will research the link text). Researching ({model_label})…"
                )
        else:
            await message.reply(
                f"Got it! Researching now ({model_label}). "
                f"I'll ping you when the report is ready."
            )

    note = await create_note(
        raw_text=raw_text,
        source=source,
        discord_user_id=str(message.author.id),
        discord_channel_id=str(message.channel.id),
        discord_message_id=str(message.id),
        requested_provider=requested_provider,
    )
    logger.info(
        "Created note %s (source=%s user=%s len=%d flags=%s)",
        note.id, source, message.author.id, len(raw_text), flags,
    )

    asyncio.create_task(
        process_note(note.id, settings, bot, pipeline_flags=flags),
        name=f"process_note_{note.id}",
    )
