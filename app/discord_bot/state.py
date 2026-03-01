from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class BotState:
    """
    Mutable bot configuration that persists across restarts.
    Stored in data/bot_state.json.
    """
    listen_channel_ids: set[int] = field(default_factory=set)
    extra_allowed_user_ids: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        self._path: str = "data/bot_state.json"

    def save(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(
                {
                    "listen_channel_ids": sorted(self.listen_channel_ids),
                    "extra_allowed_user_ids": sorted(self.extra_allowed_user_ids),
                },
                f,
                indent=2,
            )
        logger.debug("Bot state saved to %s", self._path)

    @classmethod
    def load(cls, path: str = "data/bot_state.json") -> BotState:
        state = cls()
        state._path = path
        try:
            with open(path) as f:
                data = json.load(f)
            state.listen_channel_ids = set(data.get("listen_channel_ids", []))
            state.extra_allowed_user_ids = set(data.get("extra_allowed_user_ids", []))
            logger.info(
                "Bot state loaded: %d listen channels, %d extra allowed users",
                len(state.listen_channel_ids),
                len(state.extra_allowed_user_ids),
            )
        except FileNotFoundError:
            logger.info("No bot state file at %s — starting fresh", path)
        except Exception as e:
            logger.warning("Could not load bot state: %s", e)
        return state

    def is_allowed(self, user_id: int, config_ids: list[int]) -> bool:
        """Return True if this user may submit notes."""
        # Empty config + no dynamic list = open access
        if not config_ids and not self.extra_allowed_user_ids:
            return True
        return user_id in config_ids or user_id in self.extra_allowed_user_ids
