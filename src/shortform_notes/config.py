"""Runtime settings, all from environment variables (CLI flags override them).

Everything is optional. shortform-notes picks the best available backend for each
LLM step, so it works with:

* an API key           OPENAI_API_KEY and/or ANTHROPIC_API_KEY
* a coding-agent CLI   ``claude`` (Claude Code) or ``codex`` on PATH; summaries
                       run on that CLI's subscription, no key needed
* nothing at all       caption and metadata note; add ``[local]`` for an offline
                       Whisper transcript
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

DEFAULT_OUTPUT_DIR = "reels"
# Written by the setup UI (`shortform-notes web`); real environment variables always win over it.
CONFIG_PATH = Path(os.environ.get("SHORTFORM_NOTES_CONFIG") or "~/.config/shortform-notes/config.env").expanduser()
DEFAULT_OPENAI_TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
DEFAULT_OPENAI_SUMMARY_MODEL = "gpt-4o-mini"
DEFAULT_ANTHROPIC_SUMMARY_MODEL = "claude-opus-5"
DEFAULT_WHISPER_MODEL = "base"

SUMMARY_PROVIDERS = ("openai", "anthropic", "claude-code", "codex", "none")
# Every summary backend takes images: the APIs as image blocks, `claude -p` via its
# stream-json stdin, `codex exec` via `-i`. Only "none", which makes no call at all, cannot.
VISION_SUMMARY_PROVIDERS = ("openai", "anthropic", "claude-code", "codex")
OCR_PROVIDERS = ("local", "openai", "anthropic")
DEFAULT_OCR_FPS = 1.0  # one frame per second; 0 means every frame
DEFAULT_OCR_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_OCR_ANTHROPIC_MODEL = "claude-opus-5"
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
    claude_code_model: str | None  # None means the CLI's own default
    codex_model: str | None
    whisper_model: str
    audience: str
    ocr: bool  # read on-screen text from sampled video frames (costs more time, and money on API backends)
    ocr_provider: str  # one of OCR_PROVIDERS
    ocr_fps: float  # frames sampled per second, for OCR and vision alike; 0 = every frame
    ocr_openai_model: str
    ocr_anthropic_model: str  # who the note is for; shapes the summary prompt
    vision: bool  # attach the sampled frames to the summary call so the model sees the video
    fps_explicit: bool  # a rate was asked for, so use it instead of ffmpeg's cut-aware sampling

    @property
    def can_transcribe(self) -> bool:
        return self.transcribe_provider != "none"

    @property
    def can_see_video(self) -> bool:
        """Vision was asked for *and* a summary backend that can read images is selected."""
        return self.vision and self.summary_provider in VISION_SUMMARY_PROVIDERS

    @property
    def vision_is_metered(self) -> bool:
        """True when frames cost money per call; the CLI backends bill to a subscription."""
        return self.summary_provider in ("openai", "anthropic")

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
    """Free-to-run first: a flat-rate CLI the user already pays for beats spending per token.

    "No API key required" is the tool's whole pitch, so auto-detection must never
    quietly bill an API when ``claude`` or ``codex`` is sitting on PATH. A key is
    still used when it is the only thing available, and ``--summary`` overrides all of it.
    """
    if shutil.which("claude"):
        return "claude-code"
    if shutil.which("codex"):
        return "codex"
    if openai_key and _has_module("openai"):
        return "openai"
    if anthropic_key and _has_module("anthropic"):
        return "anthropic"
    return "none"


def detect_transcribe_provider(openai_key: str | None) -> str:
    if openai_key and _has_module("openai"):
        return "openai"
    if _has_module("faster_whisper"):
        return "local"
    return "none"


def read_config_file(path: Path | None = None) -> dict[str, str]:
    """Parse a KEY=VALUE file (comments and blank lines ignored). A missing file yields {}."""
    path = path or CONFIG_PATH  # resolved at call time so tests (and SHORTFORM_NOTES_CONFIG) can redirect it
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def write_config_file(values: dict[str, str], path: Path | None = None) -> Path:
    """Write KEY=VALUE pairs (empty values dropped), owner-readable only since it may hold API keys."""
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# shortform-notes configuration, written by shortform-notes setup. Edit freely.", ""]
    lines += [f"{k}={v}" for k, v in sorted(values.items()) if v]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _env() -> dict[str, str]:
    """Config file values overlaid by the real environment (env wins)."""
    return {**read_config_file(), **os.environ}


def _validate(value: str, allowed: tuple[str, ...], what: str) -> str:
    value = value.lower().strip()
    if value not in allowed:
        raise ValueError(f"unknown {what} {value!r}; use one of: {', '.join(allowed)}")
    return value


def load_settings(
    output_dir: str | None = None,
    summary_provider: str | None = None,
    transcribe_provider: str | None = None,
    ocr: bool | None = None,
    ocr_provider: str | None = None,
    ocr_fps: float | None = None,
    vision: bool | None = None,
) -> Settings:
    """Build settings from env, with optional explicit overrides (CLI flags win)."""
    env = _env()
    openai_key = env.get("OPENAI_API_KEY") or None
    anthropic_key = env.get("ANTHROPIC_API_KEY") or None

    summary = summary_provider or env.get("SHORTFORM_NOTES_SUMMARY_PROVIDER") or "auto"
    summary = detect_summary_provider(openai_key, anthropic_key) if summary == "auto" else summary
    transcribe = transcribe_provider or env.get("SHORTFORM_NOTES_TRANSCRIBE_PROVIDER") or "auto"
    if env.get("SHORTFORM_NOTES_TRANSCRIBE", "1").lower() in _FALSE:  # legacy off switch
        transcribe = "none"
    transcribe = detect_transcribe_provider(openai_key) if transcribe == "auto" else transcribe

    ocr_on = (env.get("SHORTFORM_NOTES_OCR", "0").lower() not in _FALSE) if ocr is None else ocr
    ocr_backend = ocr_provider or env.get("SHORTFORM_NOTES_OCR_PROVIDER") or "auto"
    if ocr_backend == "auto":
        ocr_backend = "openai" if openai_key and _has_module("openai") else "local"
    fps_raw = env.get("SHORTFORM_NOTES_OCR_FPS", "")
    fps = float(fps_raw) if ocr_fps is None and fps_raw else (DEFAULT_OCR_FPS if ocr_fps is None else ocr_fps)
    vision_on = (env.get("SHORTFORM_NOTES_VISION", "0").lower() not in _FALSE) if vision is None else vision
    return Settings(
        output_dir=Path(output_dir or env.get("SHORTFORM_NOTES_DIR") or DEFAULT_OUTPUT_DIR).expanduser(),
        openai_api_key=openai_key,
        anthropic_api_key=anthropic_key,
        summary_provider=_validate(summary, SUMMARY_PROVIDERS, "summary provider"),
        transcribe_provider=_validate(transcribe, TRANSCRIBE_PROVIDERS, "transcribe provider"),
        openai_transcribe_model=env.get("SHORTFORM_NOTES_TRANSCRIBE_MODEL", DEFAULT_OPENAI_TRANSCRIBE_MODEL),
        openai_summary_model=env.get("SHORTFORM_NOTES_OPENAI_MODEL", DEFAULT_OPENAI_SUMMARY_MODEL),
        anthropic_summary_model=env.get("SHORTFORM_NOTES_ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_SUMMARY_MODEL),
        claude_code_model=env.get("SHORTFORM_NOTES_CLAUDE_CODE_MODEL") or None,
        codex_model=env.get("SHORTFORM_NOTES_CODEX_MODEL") or None,
        whisper_model=env.get("SHORTFORM_NOTES_WHISPER_MODEL", DEFAULT_WHISPER_MODEL),
        audience=env.get("SHORTFORM_NOTES_AUDIENCE", "the reader"),
        ocr=ocr_on,
        ocr_provider=_validate(ocr_backend, OCR_PROVIDERS, "OCR provider"),
        ocr_fps=max(0.0, fps),
        ocr_openai_model=env.get("SHORTFORM_NOTES_OCR_OPENAI_MODEL", DEFAULT_OCR_OPENAI_MODEL),
        ocr_anthropic_model=env.get("SHORTFORM_NOTES_OCR_ANTHROPIC_MODEL", DEFAULT_OCR_ANTHROPIC_MODEL),
        vision=vision_on,
        fps_explicit=ocr_fps is not None or bool(fps_raw),
    )
