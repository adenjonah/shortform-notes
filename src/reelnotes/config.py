"""Runtime settings, all from environment variables (or CLI flags that override them).

Everything is optional. With no API keys set, reelnotes still produces a note
from the caption and metadata — it just skips transcription and the summary.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_OUTPUT_DIR = "reels"
DEFAULT_OPENAI_TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
DEFAULT_OPENAI_SUMMARY_MODEL = "gpt-4o-mini"
DEFAULT_ANTHROPIC_SUMMARY_MODEL = "claude-opus-5"


@dataclass(frozen=True)
class Settings:
    output_dir: Path
    openai_api_key: str | None
    anthropic_api_key: str | None
    summary_provider: str  # "openai" | "anthropic" | "none"
    transcribe: bool
    openai_transcribe_model: str
    openai_summary_model: str
    anthropic_summary_model: str
    audience: str  # who the note is for; shapes the summary prompt

    @property
    def can_transcribe(self) -> bool:
        return self.transcribe and bool(self.openai_api_key)

    @property
    def can_summarize(self) -> bool:
        if self.summary_provider == "openai":
            return bool(self.openai_api_key)
        if self.summary_provider == "anthropic":
            return bool(self.anthropic_api_key)
        return False


def _pick_provider(explicit: str | None, openai_key: str | None, anthropic_key: str | None) -> str:
    if explicit:
        return explicit.lower()
    if openai_key:
        return "openai"
    if anthropic_key:
        return "anthropic"
    return "none"


def load_settings(
    output_dir: str | None = None,
    summary_provider: str | None = None,
    transcribe: bool | None = None,
) -> Settings:
    """Build settings from env, with optional explicit overrides (CLI flags win)."""
    openai_key = os.environ.get("OPENAI_API_KEY") or None
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or None
    provider = _pick_provider(
        summary_provider or os.environ.get("REELNOTES_SUMMARY_PROVIDER"), openai_key, anthropic_key
    )
    if provider not in {"openai", "anthropic", "none"}:
        raise ValueError(f"unknown summary provider {provider!r}; use openai, anthropic or none")
    env_transcribe = os.environ.get("REELNOTES_TRANSCRIBE", "1").lower() not in {"0", "false", "no"}
    return Settings(
        output_dir=Path(output_dir or os.environ.get("REELNOTES_DIR") or DEFAULT_OUTPUT_DIR).expanduser(),
        openai_api_key=openai_key,
        anthropic_api_key=anthropic_key,
        summary_provider=provider,
        transcribe=env_transcribe if transcribe is None else transcribe,
        openai_transcribe_model=os.environ.get("REELNOTES_TRANSCRIBE_MODEL", DEFAULT_OPENAI_TRANSCRIBE_MODEL),
        openai_summary_model=os.environ.get("REELNOTES_OPENAI_MODEL", DEFAULT_OPENAI_SUMMARY_MODEL),
        anthropic_summary_model=os.environ.get("REELNOTES_ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_SUMMARY_MODEL),
        audience=os.environ.get("REELNOTES_AUDIENCE", "the reader"),
    )
