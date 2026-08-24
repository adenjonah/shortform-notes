# reelnotes

Python 3.10+ package, `src/` layout. `pip install -e ".[all,dev]"` then `pytest -q` and `ruff check src tests`.

Pipeline: `urls.py` (detect) → `instagram.py` (caption via captioned-embed, key-free) + `media.py` (yt-dlp audio) → `transcribe.py` (OpenAI API or local faster-whisper) → `summarize.py` (OpenAI / Anthropic API, or `claude -p` / `codex exec` subprocess — same JSON schema) → `note.py` (Markdown) — orchestrated by `pipeline.py`. Every step degrades gracefully; the note records which `sources` produced it.

Rules:
- Never pin `yt-dlp`; its Instagram extractor breaks and is fixed in point releases.
- Never set a custom User-Agent for yt-dlp (Instagram rejects mismatched fingerprints).
- Instagram embed HTTP 200 means nothing — check for the `contextJSON` payload.
- Tests must not hit the network or spawn CLIs: patch `instagram.fetch_embed`, `media.download_media`, `summarize._run_cli`, and the `_summarize_*` functions.
- Backend selection lives in `config.detect_*_provider`; `summarize._BACKENDS` maps names to function *names* (late-bound so tests can patch).

Slash command `/reel <url>` lives in `.claude/skills/reel/`; `.mcp.json` exposes the pipeline as an MCP tool (`reelnotes mcp`).
