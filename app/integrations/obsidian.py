from __future__ import annotations

"""
Obsidian vault sync integration (4c).

After each completed note, writes {title}.md into the configured
obsidian_vault_path directory with Obsidian-compatible YAML frontmatter.
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _safe_filename(title: str) -> str:
    """Sanitise a title into a safe filename (no special chars)."""
    # Replace characters not safe in filenames
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", title)
    safe = safe.strip(". ")
    return safe[:200] or "Untitled"


def sync_to_obsidian(
    *,
    vault_path: str,
    note_id: str,
    title: str,
    project_type: str,
    confidence: float,
    report_markdown: str,
    llm_provider: str,
    created_at: Optional[datetime] = None,
    source_urls: Optional[list[str]] = None,
) -> Optional[Path]:
    """
    Write a completed note to the Obsidian vault.

    Returns the path of the written file, or None if writing failed.
    """
    try:
        vault = Path(vault_path).expanduser()
        if not vault.exists():
            logger.warning(
                "Obsidian vault path does not exist: %s — creating it", vault
            )
            vault.mkdir(parents=True, exist_ok=True)

        safe_title = _safe_filename(title)
        target = vault / f"{safe_title}.md"

        # Build YAML frontmatter
        created_str = (
            (created_at or datetime.now(timezone.utc))
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        tags = ["note-expander", project_type] if project_type else ["note-expander"]
        sources_yaml = ""
        if source_urls:
            sources_yaml = "source_urls:\n" + "".join(
                f"  - {u}\n" for u in source_urls
            )

        frontmatter = (
            "---\n"
            f"note_id: {note_id}\n"
            f"title: \"{safe_title.replace(chr(34), chr(39))}\"\n"
            f"type: {project_type}\n"
            f"confidence: {confidence:.2f}\n"
            f"llm_provider: {llm_provider}\n"
            f"created: {created_str}\n"
            f"tags: [{', '.join(tags)}]\n"
            f"{sources_yaml}"
            "---\n\n"
        )

        target.write_text(frontmatter + report_markdown, encoding="utf-8")
        logger.info("Synced note %s to Obsidian: %s", note_id, target)
        return target

    except Exception as e:
        logger.warning("Obsidian sync failed for note %s: %s", note_id, e)
        return None
