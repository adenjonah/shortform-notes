# shortform-notes

Python 3.10+ package, `src/` layout. `pip install -e ".[all,dev]"` then `pytest -q` and `ruff check src tests`.

Pipeline, orchestrated by `pipeline.py`: `urls.py` (detect), then `instagram.py` (caption via captioned-embed, key-free) plus `media.py` (yt-dlp audio, or the mp4 when frames are wanted), then `transcribe.py` (OpenAI API or local faster-whisper), optionally `ocr.py` (sample and de-duplicate frames — at the video's cuts via ffmpeg keyframes when the binary is there, else at `--ocr-fps` via OpenCV — and read on-screen text), then `summarize.py` (OpenAI / Anthropic API, or `claude -p` / `codex exec` subprocess, all returning the same JSON schema) — with `--vision` the sampled frames are tiled into timestamped contact sheets and attached to that summary call on every backend but `none`, which then also returns `scenes` for the note's "Video breakdown" section — then `note.py` (Markdown). Every step degrades gracefully; the note records which `sources` produced it.

Rules:
- Never pin `yt-dlp`; its Instagram extractor breaks and is fixed in point releases.
- Never set a custom User-Agent for yt-dlp (Instagram rejects mismatched fingerprints).
- Instagram embed HTTP 200 means nothing; check for the `contextJSON` payload.
- Tests must not hit the network or spawn CLIs: patch `instagram.fetch_embed`, `media.download_media`, `ocr.sample_frames` (or `ocr._run_ffmpeg`, the ffmpeg boundary), `summarize._run_cli`, and the `_summarize_*` functions. To assert on an API payload, stub the `openai` / `anthropic` module in `sys.modules` (see `tests/test_vision.py`) rather than requiring the SDK.
- Backend selection lives in `config.detect_*_provider`; `summarize._BACKENDS` maps names to function *names* (late-bound so tests can patch).
- The OpenAI Chat Completions parameters drift with the model generation: the GPT-5 family (the current default, `gpt-5-mini`) rejects `max_tokens` with a 400 and accepts only the default `temperature`. Use `max_completion_tokens`, and size it for reasoning tokens as well as the reply. Image tokens are billed per 32x32 patch there, not by gpt-4o's base+tile arithmetic — `ocr.openai_image_tokens` holds that formula.
- The agent CLIs' flags drift: `claude -p` needs `--input-format stream-json` *plus* `--output-format stream-json` *plus* `--verbose` to accept images, and `codex exec` dropped `--ask-for-approval` in 0.147 and needs `--skip-git-repo-check` to run outside a repo. Check `--help` against the installed CLI before trusting an invocation.

Slash command `/reel <url>` lives in `.claude/skills/reel/`; `.mcp.json` exposes the pipeline as an MCP tool (`shortform-notes mcp`).
