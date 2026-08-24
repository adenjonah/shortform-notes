"""Terminal wizard writes the same config the web page does."""

from reelnotes import config
from reelnotes.setup_cli import run_setup


def test_wizard_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.env")
    answers = iter(["", "", str(tmp_path / "notes"), ""])
    path = run_setup(ask=lambda _prompt: next(answers), ask_secret=lambda _p: "unused")
    saved = config.read_config_file(path)
    assert saved["REELNOTES_SUMMARY_PROVIDER"] == "claude-code"
    assert saved["REELNOTES_TRANSCRIBE_PROVIDER"] == "local"
    assert saved["REELNOTES_DIR"] == str(tmp_path / "notes")
    assert (tmp_path / "notes").is_dir()
    assert "OPENAI_API_KEY" not in saved


def test_wizard_openai_key_and_reprompt(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.env")
    answers = iter(["9", "3", "1", str(tmp_path / "n"), "a home cook"])
    secrets = iter(["", "sk-test"])  # first empty answer is rejected
    path = run_setup(ask=lambda _p: next(answers), ask_secret=lambda _p: next(secrets))
    saved = config.read_config_file(path)
    assert saved["REELNOTES_SUMMARY_PROVIDER"] == "openai"
    assert saved["OPENAI_API_KEY"] == "sk-test"
    assert saved["REELNOTES_TRANSCRIBE_PROVIDER"] == "openai"
    assert saved["REELNOTES_AUDIENCE"] == "a home cook"
