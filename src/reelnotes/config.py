"""Runtime settings, all from environment variables (CLI flags override them).

Everything is optional. reelnotes picks the best available backend for each
LLM step, so it works with:

* an API key           OPENAI_API_KEY and/or ANTHROPIC_API_KEY
* a coding-agent CLI   ``claude`` (Claude Code) or ``codex`` on PATH — summaries
                       run on the subscription you already pay for, no key needed
* nothing at all       caption + metadata note; add ``[local]`` for an offline
                       Whisper transcript
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

DEFAULT_OUTPUT_DIR = "reels"
DEFAULT_OPENAI_TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
DEFAULT_OPENAI_SUMMARY_MODEL = "gpt-4o-mini"
DEFAULT_ANTHROPIC_SUMMARY_MODEL = "claude-opus-5"
DEFAULT_WHISPER_MODEL = "base"

SUMMARY_PROVIDERS = ("openai", "anthropic", "claude-code", "codex", "none")
TRANSCRIBE_PROVIDERS = ("openai", "local", "none")
_FALSE = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Settings:
    output_dir: Path
    openai_api_key: str | None
    anthropic_api_key: str | None
    summary_provider: str  # one of SUMMARY_PROVIDERS
    transcribe_provider: str  # one of TRANSCRIBE_PROVIDERS
    openai_transcribe_model: str
    openai_summary_model: str
    anthropic_summary_model: str
    claude_code_model: str | None  # None → the CLI's own default
    codex_model: str | None
    whisper_model: str
    audience: str  # who the note is for; shapes the summary prompt

    @property
    def can_transcribe(self) -> bool:
        return self.transcribe_provider != "none"

    @property
    def can_summarize(self) -> bool:
        return self.summary_provider != "none"


def _has_module(name: str) -> bool:
    from importlib.util import find_spec

    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def detect_summary_provider(openai_key: str | None, anthropic_key: str | None) -> str:
    """Cheapest-to-set-up first: keys, then coding-agent CLIs already on PATH."""
    if openai_key and _has_module("openai"):
        return "openai"
    if anthropic_key and _has_module("anthropic"):
        return "anthropic"
    if shutil.which("claude"):
        return "claude-code"
    if shutil.which("codex"):
        return "codex"
    return "none"


def detect_transcribe_provider(openai_key: str | None) -> str:
    if openai_key and _has_module("openai"):
        return "openai"
    if _has_module("faster_whisper"):
        return "local"
    return "none"


def _validate(value: str, allowed: tuple[str, ...], what: str) -> str:
    value = value.lower().strip()
    if value not in allowed:
        raise ValueError(f"unknown {what} {value!r}; use one of: {', '.join(allowed)}")
    return value


def load_settings(
    output_dir: str | None = None,
    summary_provider: str | None = None,
    transcribe_provider: str | None = None,
) -> Settings:
    """Build settings from env, with optional explicit overrides (CLI flags win)."""
    openai_key = os.environ.get("OPENAI_API_KEY") or None
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or None

    summary = summary_provider or os.environ.get("REELNOTES_SUMMARY_PROVIDER") or "auto"
    summary = detect_summary_provider(openai_key, anthropic_key) if summary == "auto" else summary
    transcribe = transcribe_provider or os.environ.get("REELNOTES_TRANSCRIBE_PROVIDER") or "auto"
    if os.environ.get("REELNOTES_TRANSCRIBE", "1").lower() in _FALSE:  # legacy off switch
        transcribe = "none"
    transcribe = detect_transcribe_provider(openai_key) if transcribe == "auto" else transcribe

    return Settings(
        output_dir=Path(output_dir or os.environ.get("REELNOTES_DIR") or DEFAULT_OUTPUT_DIR).expanduser(),
        openai_api_key=openai_key,
        anthropic_api_key=anthropic_key,
        summary_provider=_validate(summary, SUMMARY_PROVIDERS, "summary provider"),
        transcribe_provider=_validate(transcribe, TRANSCRIBE_PROVIDERS, "transcribe provider"),
        openai_transcribe_model=os.environ.get("REELNOTES_TRANSCRIBE_MODEL", DEFAULT_OPENAI_TRANSCRIBE_MODEL),
        openai_summary_model=os.environ.get("REELNOTES_OPENAI_MODEL", DEFAULT_OPENAI_SUMMARY_MODEL),
        anthropic_summary_model=os.environ.get("REELNOTES_ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_SUMMARY_MODEL),
        claude_code_model=os.environ.get("REELNOTES_CLAUDE_CODE_MODEL") or None,
        codex_model=os.environ.get("REELNOTES_CODEX_MODEL") or None,
        whisper_model=os.environ.get("REELNOTES_WHISPER_MODEL", DEFAULT_WHISPER_MODEL),
        audience=os.environ.get("REELNOTES_AUDIENCE", "the reader"),
    )
