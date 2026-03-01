"""
Thread-safe in-memory progress tracker for the 5-stage LLM pipeline.

crew.py calls start()/complete() from a worker thread.
Dashboard routes call get() from the async event loop.
The threading.Lock keeps both sides consistent.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

STAGE_ORDER = ["classify", "query_gen", "research", "analyse", "format"]

STAGE_LABELS = {
    "classify": "Classifying idea",
    "query_gen": "Searching the web",
    "research": "Synthesizing research",
    "analyse": "Analysing findings",
    "format": "Formatting report",
}


@dataclass
class StageState:
    status: str = "waiting"  # waiting | running | complete | skipped
    summary: str = ""
    started_at: Optional[float] = field(default=None)
    finished_at: Optional[float] = field(default=None)

    def elapsed(self) -> Optional[float]:
        if self.started_at is None:
            return None
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return end - self.started_at


_lock = threading.Lock()
# note_id → {stage_key → StageState}
_progress: dict[str, dict[str, StageState]] = {}


def init(note_id: str) -> None:
    """Initialise a fresh progress record for a note."""
    with _lock:
        _progress[note_id] = {stage: StageState() for stage in STAGE_ORDER}


def start(note_id: str, stage: str) -> None:
    with _lock:
        stages = _progress.get(note_id)
        if stages and stage in stages:
            stages[stage].status = "running"
            stages[stage].started_at = time.monotonic()


def complete(note_id: str, stage: str, summary: str = "") -> None:
    with _lock:
        stages = _progress.get(note_id)
        if stages and stage in stages:
            stages[stage].status = "complete"
            stages[stage].finished_at = time.monotonic()
            stages[stage].summary = summary[:400] if summary else ""


def skip(note_id: str, stage: str, reason: str = "") -> None:
    with _lock:
        stages = _progress.get(note_id)
        if stages and stage in stages:
            stages[stage].status = "skipped"
            stages[stage].summary = reason


def get(note_id: str) -> Optional[dict[str, StageState]]:
    """Return a shallow copy of the stage dict, or None if not initialised."""
    with _lock:
        stages = _progress.get(note_id)
        if stages is None:
            return None
        return dict(stages)
