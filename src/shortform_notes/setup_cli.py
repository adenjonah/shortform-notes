"""Terminal setup wizard: ``shortform-notes setup``.

Asks the same questions as the web page (summary backend, transcription,
folder, audience) and writes the same config file, so either path leaves the
tool in an identical state.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

from shortform_notes import config

SUMMARY_CHOICES = [
    ("claude-code", "Claude Code", "uses your Claude subscription through the claude CLI, no API key"),
    ("codex", "Codex CLI", "uses your ChatGPT subscription through the codex CLI, no API key"),
    ("openai", "OpenAI API key", "pay per use, about $0.001 per reel; also enables the best transcription"),
    ("anthropic", "Anthropic API key", "pay per use with Claude via the API"),
    ("none", "No summary", "save the caption, transcript and metadata only"),
]
OCR_CHOICES = [
    ("off", "Off", "caption and transcript only (default)"),
    ("local", "On, local OCR", "free, runs on this computer; slower per reel"),
    ("openai", "On, OpenAI vision", "about $0.013 per 30 s video at 1 frame per second"),
    ("anthropic", "On, Claude vision", "about $0.18 per 30 s video at 1 frame per second with claude-opus-5"),
]
TRANSCRIBE_CHOICES = [
    ("openai", "OpenAI", "about $0.003 per minute of video; needs an OpenAI API key"),
    ("local", "Offline on this computer", "free and private; the first run downloads a 75 MB model"),
    ("none", "Skip transcripts", "caption and metadata only"),
]


def _say(text: str = "") -> None:
    print(text, flush=True)


def _pick(question: str, choices: list[tuple[str, str, str]], default: str, ask=input) -> str:
    _say(question)
    for i, (_, title, desc) in enumerate(choices, 1):
        _say(f"  {i}) {title}: {desc}")
    ids = [c[0] for c in choices]
    default_index = ids.index(default) + 1
    while True:
        raw = ask(f"Choose 1-{len(choices)} [{default_index}]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return ids[int(raw) - 1]
        _say("Enter a number from the list.")


def _ask_secret(label: str, existing: str, ask_secret=getpass.getpass) -> str:
    if existing:
        value = ask_secret(f"{label} (press Enter to keep the saved one): ").strip()
        return value or existing
    while True:
        value = ask_secret(f"{label}: ").strip()
        if value:
            return value
        _say("A key is required for this option.")


def run_setup(ask=input, ask_secret=getpass.getpass) -> Path:
    """Interactive wizard. ``ask``/``ask_secret`` are injectable for tests."""
    current = config.read_config_file()
    _say("shortform-notes setup")
    _say("Answers are saved to " + str(config.CONFIG_PATH) + ". Press Enter to accept a default.")
    _say()

    summary = _pick(
        "1/4  Where should the summary run?",
        SUMMARY_CHOICES,
        current.get("SHORTFORM_NOTES_SUMMARY_PROVIDER") or "claude-code",
        ask,
    )
    openai_key = current.get("OPENAI_API_KEY", "")
    anthropic_key = current.get("ANTHROPIC_API_KEY", "")
    if summary == "openai":
        openai_key = _ask_secret("OpenAI API key", openai_key, ask_secret)
    elif summary == "anthropic":
        anthropic_key = _ask_secret("Anthropic API key", anthropic_key, ask_secret)
    _say()

    transcribe_default = current.get("SHORTFORM_NOTES_TRANSCRIBE_PROVIDER") or ("openai" if openai_key else "local")
    transcribe = _pick("2/4  How should audio be transcribed?", TRANSCRIBE_CHOICES, transcribe_default, ask)
    if transcribe == "openai" and not openai_key:
        openai_key = _ask_secret("OpenAI API key", "", ask_secret)
    _say()

    ocr_default = (
        current.get("SHORTFORM_NOTES_OCR_PROVIDER", "off") if current.get("SHORTFORM_NOTES_OCR") == "1" else "off"
    )
    _say("Optional: read on-screen text from video frames. This downloads the full video and reads")
    _say("one frame per second (change with SHORTFORM_NOTES_OCR_FPS; 0 means every frame, which costs")
    _say("about 30 times more on a paid backend).")
    ocr_choice = _pick("Read on-screen text?", OCR_CHOICES, ocr_default, ask)
    if ocr_choice == "openai" and not openai_key:
        openai_key = _ask_secret("OpenAI API key", "", ask_secret)
    if ocr_choice == "anthropic" and not anthropic_key:
        anthropic_key = _ask_secret("Anthropic API key", "", ask_secret)
    _say()

    default_dir = current.get("SHORTFORM_NOTES_DIR") or str(Path.home() / "shortform-notes")
    folder = ask(f"3/4  Folder for notes [{default_dir}]: ").strip() or default_dir
    Path(folder).expanduser().mkdir(parents=True, exist_ok=True)
    _say()

    default_audience = current.get("SHORTFORM_NOTES_AUDIENCE", "")
    audience = ask(
        f"4/4  Who are the notes for? Optional, shapes the summary [{default_audience or 'the reader'}]: "
    ).strip()
    audience = audience or default_audience
    _say()

    path = config.write_config_file(
        {
            **current,
            "SHORTFORM_NOTES_SUMMARY_PROVIDER": summary,
            "SHORTFORM_NOTES_TRANSCRIBE_PROVIDER": transcribe,
            "SHORTFORM_NOTES_OCR": "1" if ocr_choice != "off" else "",
            "SHORTFORM_NOTES_OCR_PROVIDER": ocr_choice if ocr_choice != "off" else "",
            "SHORTFORM_NOTES_DIR": folder,
            "SHORTFORM_NOTES_AUDIENCE": audience,
            "OPENAI_API_KEY": openai_key,
            "ANTHROPIC_API_KEY": anthropic_key,
        }
    )
    _say(f"Saved {path}")
    _say()
    _say("Next: import a link with")
    _say("  shortform-notes https://www.instagram.com/p/DS3DPehEnpA/")
    if summary in ("claude-code", "codex"):
        tool = "Claude Code" if summary == "claude-code" else "Codex"
        _say(f"To use it inside {tool}, run: shortform-notes web  and copy the prompt on the last page.")
    return path


def main() -> int:
    if not sys.stdin.isatty():
        _say("shortform-notes setup needs an interactive terminal. Run: shortform-notes web")
        return 2
    try:
        run_setup()
    except (KeyboardInterrupt, EOFError):
        _say("\nSetup cancelled. Nothing was saved.")
        return 1
    return 0
