"""Provider detection and the coding-agent CLI backends (subprocess is faked)."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from shortform_notes import config, summarize
from shortform_notes.summarize import SummaryError, extract_json


def settings(**overrides) -> config.Settings:
    base = dict(
        output_dir="reels",
        openai_api_key=None,
        anthropic_api_key=None,
        summary_provider="claude-code",
        transcribe_provider="none",
        openai_transcribe_model="x",
        openai_summary_model="x",
        anthropic_summary_model="x",
        claude_code_model=None,
        codex_model=None,
        whisper_model="base",
        audience="the reader",
    )
    return config.Settings(**{**base, **overrides})


# ── detection ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "openai_key,anthropic_key,on_path,expected",
    [
        ("sk", None, {"claude"}, "openai"),
        (None, "sk-ant", {"claude"}, "anthropic"),
        (None, None, {"claude", "codex"}, "claude-code"),
        (None, None, {"codex"}, "codex"),
        (None, None, set(), "none"),
    ],
)
def test_detect_summary_provider(openai_key, anthropic_key, on_path, expected):
    with (
        patch.object(config.shutil, "which", lambda name: f"/bin/{name}" if name in on_path else None),
        patch.object(config, "_has_module", lambda name: name in {"openai", "anthropic"}),
    ):
        assert config.detect_summary_provider(openai_key, anthropic_key) == expected


def test_detect_transcribe_provider():
    with patch.object(config, "_has_module", lambda name: name in {"openai", "faster_whisper"}):
        assert config.detect_transcribe_provider("sk") == "openai"
        assert config.detect_transcribe_provider(None) == "local"
    with patch.object(config, "_has_module", lambda name: False):
        assert config.detect_transcribe_provider(None) == "none"


def test_load_settings_rejects_unknown_provider(monkeypatch):
    monkeypatch.setenv("SHORTFORM_NOTES_SUMMARY_PROVIDER", "gemini")
    with pytest.raises(ValueError, match="summary provider"):
        config.load_settings()


def test_load_settings_explicit_overrides_env(monkeypatch):
    monkeypatch.setenv("SHORTFORM_NOTES_SUMMARY_PROVIDER", "none")
    monkeypatch.setenv("OPENAI_API_KEY", "sk")
    s = config.load_settings(summary_provider="codex", transcribe_provider="none")
    assert s.summary_provider == "codex"
    assert s.transcribe_provider == "none"


# ── JSON extraction ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        '{"title": "T", "summary": "S", "takeaways": ["a"]}',
        '```json\n{"title": "T", "summary": "S", "takeaways": ["a"]}\n```',
        'Sure! Here you go:\n{"title": "T", "summary": "S", "takeaways": ["a"]}\nHope that helps.',
    ],
)
def test_extract_json_lenient(text):
    assert extract_json(text)["title"] == "T"


def test_extract_json_no_object():
    with pytest.raises(SummaryError):
        extract_json("I cannot help with that.")


# ── CLI backends ───────────────────────────────────────────────────────


def test_claude_code_argv_disables_tools_and_persistence():
    argv = summarize.claude_code_argv(settings(claude_code_model="haiku"))
    assert argv[:2] == ["claude", "-p"]
    assert argv[argv.index("--tools") + 1] == ""
    assert "--no-session-persistence" in argv and "--disable-slash-commands" in argv
    assert argv[argv.index("--model") + 1] == "haiku"


def test_codex_argv_is_read_only_and_reads_stdin():
    argv = summarize.codex_argv(settings(summary_provider="codex"), "/tmp/last.txt")
    assert argv[:2] == ["codex", "exec"]
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("--ask-for-approval") + 1] == "never"
    assert argv[argv.index("--output-last-message") + 1] == "/tmp/last.txt"
    assert argv[-1] == "-"


async def test_claude_code_backend_parses_envelope():
    envelope = json.dumps(
        {"is_error": False, "result": '```json\n{"title": "T", "summary": "S", "takeaways": ["a"]}\n```'}
    )
    with patch.object(summarize, "_run_cli", AsyncMock(return_value=envelope)) as run:
        result = await summarize.summarize("cap", None, settings())
    assert result.title == "T" and result.takeaways == ("a",)
    argv, prompt = run.await_args.args
    assert argv[0] == "claude"
    assert "Caption:\ncap" in prompt and "no code fences" in prompt


async def test_claude_code_backend_error_envelope_degrades():
    envelope = json.dumps({"is_error": True, "result": "Not logged in"})
    with patch.object(summarize, "_run_cli", AsyncMock(return_value=envelope)):
        result = await summarize.summarize("First line of caption", None, settings())
    assert result.title == "First line of caption" and result.summary == ""


async def test_codex_backend_reads_last_message_file():
    async def fake_run(argv, prompt):
        path = argv[argv.index("--output-last-message") + 1]
        Path(path).write_text('{"title": "C", "summary": "S", "takeaways": []}')
        return "ignored stdout"

    with patch.object(summarize, "_run_cli", fake_run):
        result = await summarize.summarize("cap", None, settings(summary_provider="codex"))
    assert result.title == "C"


async def test_missing_cli_degrades_gracefully():
    with patch.object(summarize.shutil, "which", lambda _: None):
        result = await summarize.summarize("cap line", None, settings(summary_provider="codex"))
    assert result.title == "cap line" and result.summary == ""
