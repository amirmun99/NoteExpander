from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_whisper_model = None


def _get_whisper_model(model_name: str):
    global _whisper_model
    if _whisper_model is None:
        import whisper
        logger.info("Loading Whisper model: %s", model_name)
        _whisper_model = whisper.load_model(model_name)
    return _whisper_model


async def transcribe_attachment(
    attachment,
    audio_dir: str,
    whisper_model_name: str = "base",
    language: str = "en",
) -> str:
    """
    Download a Discord voice attachment and transcribe it with Whisper.

    Args:
        attachment: discord.Attachment object
        audio_dir: Directory to save temp audio files
        whisper_model_name: Whisper model size (tiny/base/small/medium/large)
        language: Language hint for Whisper

    Returns:
        Transcribed text string
    """
    Path(audio_dir).mkdir(parents=True, exist_ok=True)

    # Save the attachment to a temp file
    suffix = Path(attachment.filename).suffix or ".ogg"
    tmp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            dir=audio_dir,
            suffix=suffix,
            delete=False,
        ) as tmp:
            tmp_path = tmp.name

        await attachment.save(tmp_path)
        logger.info("Downloaded voice attachment to %s (%d bytes)", tmp_path, os.path.getsize(tmp_path))

        model = _get_whisper_model(whisper_model_name)
        result = model.transcribe(tmp_path, language=language)
        text = result.get("text", "").strip()

        logger.info("Whisper transcription complete: %d chars", len(text))
        return text

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
            logger.debug("Deleted temp audio file: %s", tmp_path)
