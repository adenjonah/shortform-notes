# shortform-notes

Python 3.10+ package, `src/` layout. `pip install -e ".[all,dev]"` then `pytest -q` and `ruff check src tests`.

Pipeline, orchestrated by `pipeline.py`: `urls.py` (detect), then `instagram.py` (caption via captioned-embed, key-free) plus `media.py` (yt-dlp audio, or the mp4 when frames are wanted), then `transcribe.py` (OpenAI API or local faster-whisper), optionally `ocr.py` (sample and de-duplicate frames, read on-screen text), then `summarize.py` (OpenAI / Anthropic API, or `claude -p` / `codex exec` subprocess, all returning the same JSON schema) — with `--vision` the sampled frames are attached to that summary call on the two API backends — then `note.py` (Markdown). Every step degrades gracefully; the note records which `sources` produced it.

Rules:
- Never pin `yt-dlp`; its Instagram extractor breaks and is fixed in point releases.
- Never set a custom User-Agent for yt-dlp (Instagram rejects mismatched fingerprints).
- Instagram embed HTTP 200 means nothing; check for the `contextJSON` payload.
- Tests must not hit the network or spawn CLIs: patch `instagram.fetch_embed`, `media.download_media`, `ocr.extract_frames`, `summarize._run_cli`, and the `_summarize_*` functions. To assert on an API payload, stub the `openai` / `anthropic` module in `sys.modules` (see `tests/test_vision.py`) rather than requiring the SDK.
- Backend selection lives in `config.detect_*_provider`; `summarize._BACKENDS` maps names to function *names* (late-bound so tests can patch).

Slash command `/reel <url>` lives in `.claude/skills/reel/`; `.mcp.json` exposes the pipeline as an MCP tool (`shortform-notes mcp`).
