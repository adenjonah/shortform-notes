"""Persistent config file: written by the web UI, read by everything else; real env wins."""

from reelnotes import config


def test_write_then_read_roundtrip(tmp_path):
    path = tmp_path / "config.env"
    config.write_config_file({"REELNOTES_DIR": "/notes/reels", "OPENAI_API_KEY": "sk-x", "EMPTY": ""}, path)
    assert config.read_config_file(path) == {"REELNOTES_DIR": "/notes/reels", "OPENAI_API_KEY": "sk-x"}
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_read_ignores_comments_and_quotes(tmp_path):
    path = tmp_path / "config.env"
    path.write_text("# comment\n\nREELNOTES_AUDIENCE=\"a home cook\"\nBAD LINE\nX='y'\n")
    assert config.read_config_file(path) == {"REELNOTES_AUDIENCE": "a home cook", "X": "y"}


def test_missing_file_is_empty(tmp_path):
    assert config.read_config_file(tmp_path / "nope.env") == {}


def test_env_overrides_config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.env"
    config.write_config_file({"REELNOTES_SUMMARY_PROVIDER": "codex", "REELNOTES_DIR": "/from/file"}, path)
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("REELNOTES_DIR", "/from/env")
    s = config.load_settings(transcribe_provider="none")
    assert s.summary_provider == "codex"
    assert str(s.output_dir) == "/from/env"
