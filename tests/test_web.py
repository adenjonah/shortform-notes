"""The local setup server: state, config save, and CLI warning (no network, no scanning)."""

import json
import threading
from http.client import HTTPConnection

import pytest

from reelnotes import config
from reelnotes.web import server as web


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.env")
    srv = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    srv.shutdown()


def _json(conn, method, path, body=None):
    conn.request(method, path, body=json.dumps(body) if body else None, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    return resp.status, json.loads(resp.read())


def test_index_and_state(client):
    client.request("GET", "/")
    resp = client.getresponse()
    assert resp.status == 200 and b"Where should the summary run?" in resp.read()
    status, state = _json(client, "GET", "/api/state")
    assert status == 200
    assert state["config_exists"] is False
    assert "vaults" not in state  # no machine scanning


def test_save_config_writes_file_and_creates_dir(client, tmp_path, monkeypatch):
    monkeypatch.setattr(web.shutil, "which", lambda name: None)
    out = tmp_path / "notes" / "reels"
    status, data = _json(
        client,
        "POST",
        "/api/config",
        {"REELNOTES_SUMMARY_PROVIDER": "claude-code", "REELNOTES_DIR": str(out), "OPENAI_API_KEY": "sk-secret"},
    )
    assert status == 200
    assert out.is_dir()
    saved = config.read_config_file(config.CONFIG_PATH)
    assert saved["REELNOTES_SUMMARY_PROVIDER"] == "claude-code" and saved["OPENAI_API_KEY"] == "sk-secret"
    assert data["detect"]["has_openai_key"] is True and "OPENAI_API_KEY" not in json.dumps(data["detect"])
    assert "claude" in data["warning"]  # CLI chosen but not on PATH: warning, not a hard error


def test_import_rejects_bad_url(client):
    status, data = _json(client, "POST", "/api/import", {"url": "https://example.com/x"})
    assert status == 422 and "not a supported" in data["error"]
