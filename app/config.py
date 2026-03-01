from __future__ import annotations

import os
from dataclasses import dataclass, field  # noqa: F401 (field used in Settings)
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

load_dotenv()


@dataclass
class LLMConfig:
    provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    openai_model: str = "gpt-4o-mini"
    temperature: float = 0.3
    max_tokens: int = 4096
    timeout_seconds: int = 120
    max_retries: int = 2
    # Per-stage overrides: {"classify": {"max_tokens": 300, "temperature": 0.1}, ...}
    stage_overrides: dict = field(default_factory=dict)


@dataclass
class WhisperConfig:
    model: str = "base"
    language: str = "en"


@dataclass
class DashboardConfig:
    host: str = "0.0.0.0"
    port: int = 8765
    title: str = "Note Expander"


@dataclass
class DiscordConfig:
    allowed_user_ids: list[int] = field(default_factory=list)
    notification_channel_id: Optional[int] = None


@dataclass
class SearchConfig:
    enabled: bool = True
    max_results: int = 5


@dataclass
class ProcessingConfig:
    max_concurrent_jobs: int = 2
    audio_download_dir: str = "data/audio"
    db_path: str = "data/noteexpander.db"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/app.log"


@dataclass
class IntegrationsConfig:
    obsidian_vault_path: Optional[str] = None  # absolute or ~ path to vault dir (4c)
    webhook_url: Optional[str] = None          # POST JSON payload on completion (4b)


@dataclass
class Settings:
    llm: LLMConfig
    whisper: WhisperConfig
    dashboard: DashboardConfig
    discord: DiscordConfig
    search: SearchConfig
    processing: ProcessingConfig
    logging: LoggingConfig
    integrations: IntegrationsConfig = field(default_factory=IntegrationsConfig)

    # Secrets from .env
    discord_token: str = ""
    discord_guild_id: Optional[str] = None
    openai_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None

    @property
    def llm_model_string(self) -> str:
        """Returns the litellm model string for the configured provider."""
        if self.llm.provider == "ollama":
            return f"ollama/{self.llm.ollama_model}"
        return self.llm.openai_model

    @property
    def llm_provider_label(self) -> str:
        """Human-readable label stored in DB."""
        if self.llm.provider == "ollama":
            return f"ollama/{self.llm.ollama_model}"
        return f"openai/{self.llm.openai_model}"


def _load_yaml(path: str | Path = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _clean_id(value: Optional[str]) -> Optional[str]:
    """Return the value only if it's a pure integer string, else None."""
    if not value:
        return None
    stripped = value.strip()
    return stripped if stripped.isdigit() else None


def _clean_str(value: Optional[str]) -> Optional[str]:
    """Strip whitespace; return None for empty or comment-only values."""
    if not value:
        return None
    stripped = value.strip()
    return stripped if stripped and not stripped.startswith("#") else None


def _build_settings(raw: dict) -> Settings:
    llm_raw = raw.get("llm", {})
    whisper_raw = raw.get("whisper", {})
    dashboard_raw = raw.get("dashboard", {})
    discord_raw = raw.get("discord", {})
    search_raw = raw.get("search", {})
    processing_raw = raw.get("processing", {})
    logging_raw = raw.get("logging", {})
    integrations_raw = raw.get("integrations", {})

    return Settings(
        llm=LLMConfig(**llm_raw) if llm_raw else LLMConfig(),
        whisper=WhisperConfig(**whisper_raw) if whisper_raw else WhisperConfig(),
        dashboard=DashboardConfig(**dashboard_raw) if dashboard_raw else DashboardConfig(),
        discord=DiscordConfig(
            allowed_user_ids=discord_raw.get("allowed_user_ids", []),
            notification_channel_id=discord_raw.get("notification_channel_id"),
        ),
        search=SearchConfig(**search_raw) if search_raw else SearchConfig(),
        processing=ProcessingConfig(**processing_raw) if processing_raw else ProcessingConfig(),
        logging=LoggingConfig(**logging_raw) if logging_raw else LoggingConfig(),
        integrations=IntegrationsConfig(**integrations_raw) if integrations_raw else IntegrationsConfig(),
        discord_token=os.environ.get("DISCORD_TOKEN", ""),
        discord_guild_id=_clean_id(os.environ.get("DISCORD_GUILD_ID")),
        openai_api_key=_clean_str(os.environ.get("OPENAI_API_KEY")),
        tavily_api_key=_clean_str(os.environ.get("TAVILY_API_KEY")),
    )


@lru_cache(maxsize=1)
def get_settings(config_path: str = "config.yaml") -> Settings:
    raw = _load_yaml(config_path)
    return _build_settings(raw)
