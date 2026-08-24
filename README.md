# reelnotes

Turn Instagram Reels, TikToks and YouTube Shorts into Markdown notes — caption, transcript, and an AI summary — with one command.

```
$ reelnotes https://www.instagram.com/reel/DQCkNLtgqEe/

✓ reels/2025-10-20-teddysphotos-symmetry-with-karan-aujla.md  (sources: caption, transcript)
  Symmetry with Karan Aujla on Play Remixes EP
  The creator promotes the release of the track Symmetry featuring Karan Aujla ...
  • The song Symmetry features Karan Aujla (@karanaujla).
  • Karan Aujla taught the creator Punjabi for the video's bit.
```

**No API key required.** If you have [Claude Code](https://claude.com/claude-code) or [Codex CLI](https://developers.openai.com/codex/cli) installed, summaries run through the subscription you already pay for. Add `[local]` and transcripts run offline on your own CPU with Whisper. Or bring an OpenAI / Anthropic key and everything runs through the API for about **$0.004 per reel**.

Built for "I'll save this recipe for later" and then never opening Instagram again. Notes are plain Markdown with YAML frontmatter, so they drop straight into Obsidian, Logseq, or a folder.

## Install

```bash
pipx install "reelnotes[all] @ git+https://github.com/adenjonah/reel-notes"
```

Requires Python 3.10+. No ffmpeg needed. Extras:

| Extra | Adds | You need it if |
|---|---|---|
| *(none)* | caption + metadata, Claude Code / Codex summaries | you want zero-key mode |
| `openai` | API transcription + summaries | you have `OPENAI_API_KEY` |
| `anthropic` | Claude API summaries | you have `ANTHROPIC_API_KEY` |
| `local` | offline Whisper transcription (faster-whisper, ~75 MB model on first run) | you want transcripts without a key |
| `mcp` | MCP server for Claude Code / Claude Desktop | you want it as an editor tool |
| `all` | `openai` + `anthropic` + `mcp` | |

## Pick how the LLM steps run

Two steps need a model: **transcribing** the audio and **summarizing** the text. Each has interchangeable backends and `reelnotes` auto-detects the best one it can find. You never have to configure anything unless you want to.

```
summary     OPENAI_API_KEY  →  ANTHROPIC_API_KEY  →  `claude` on PATH  →  `codex` on PATH  →  none
transcript  OPENAI_API_KEY  →  faster-whisper installed                                    →  none
```

### Option A: your own API key

```bash
export OPENAI_API_KEY=sk-...          # transcription (gpt-4o-mini-transcribe) + summaries (gpt-4o-mini)
export ANTHROPIC_API_KEY=sk-ant-...   # summaries with Claude instead (claude-opus-5)
reelnotes <url>
```

### Option B: Claude Code or Codex (no key)

If `claude` or `codex` is on your PATH and logged in, that's it:

```bash
reelnotes <url>                        # auto-detects
reelnotes --summary claude-code <url>  # or pin one
reelnotes --summary codex <url>
```

Under the hood it's one shell-out per reel with the prompt on stdin:

- Claude Code: `claude -p --output-format json --tools "" --disable-slash-commands --no-session-persistence`
- Codex: `codex exec --sandbox read-only --ask-for-approval never --output-last-message <tmp> -`

Every tool is disabled and nothing is persisted, so the agent sees only the caption and transcript, never your files. Set `REELNOTES_CLAUDE_CODE_MODEL` / `REELNOTES_CODEX_MODEL` to pick a model; otherwise the CLI's own default is used.

### Option C: fully offline transcript

```bash
pip install "reelnotes[local]"
reelnotes --transcribe local <url>     # auto-detected once faster-whisper is installed and no OPENAI_API_KEY is set
```

`REELNOTES_WHISPER_MODEL` picks the size (`tiny` … `large-v3`, default `base`). Combine with Option B for a pipeline that never touches an API key.

### Configuration reference

Everything is an environment variable (copy `.env.example`) or a flag:

| Variable | Flag | Default | What it does |
|---|---|---|---|
| `REELNOTES_SUMMARY_PROVIDER` | `--summary` | `auto` | `openai`, `anthropic`, `claude-code`, `codex`, `none` |
| `REELNOTES_TRANSCRIBE_PROVIDER` | `--transcribe` | `auto` | `openai`, `local`, `none` |
| `REELNOTES_DIR` | `-o/--out` | `reels` | Output directory (point it at your vault) |
| `REELNOTES_AUDIENCE` | | `the reader` | Who the summary is written for, e.g. `Jonah, a home cook` |
| `REELNOTES_CLAUDE_CODE_MODEL` | | CLI default | Model for `claude -p` |
| `REELNOTES_CODEX_MODEL` | | CLI default | Model for `codex exec` |
| `REELNOTES_WHISPER_MODEL` | | `base` | faster-whisper model size |
| `REELNOTES_OPENAI_MODEL` | | `gpt-4o-mini` | OpenAI summary model |
| `REELNOTES_ANTHROPIC_MODEL` | | `claude-opus-5` | Anthropic summary model |

`reelnotes --help` lists everything. `--json` gives machine-readable output; `--no-transcript` skips the audio download.

## Supported links

| Platform | URL shapes | Caption source | Notes |
|---|---|---|---|
| Instagram | `/reel/…`, `/reels/…`, `/p/…`, `/tv/…`, `/share/reel/…` | captioned-embed endpoint (no login) | Share links are resolved to the canonical shortcode; `?igsh=` tracking is stripped |
| TikTok | `tiktok.com/@user/video/…`, `vm.tiktok.com/…`, `vt.tiktok.com/…` | yt-dlp description | Short links are followed by yt-dlp |
| YouTube Shorts | `youtube.com/shorts/…`, `youtu.be/…` | yt-dlp description | Regular `watch?v=` URLs are intentionally not matched; this is a short-video tool |

Paste several links in one command and each becomes its own note.

## What a note looks like

```markdown
---
type: reel
platform: instagram
source: https://www.instagram.com/reel/DQCkNLtgqEe/
creator: "@teddysphotos"
posted: 2025-10-20
imported: 2026-08-24
duration_seconds: 26
sources: [caption, transcript]
tags: [reel]
---

# Symmetry with Karan Aujla on Play Remixes EP

**Source:** [instagram · @teddysphotos](https://www.instagram.com/reel/DQCkNLtgqEe/)

## Summary
...

## Key takeaways
- ...

## Caption
> verbatim caption

## Transcript
verbatim transcript
```

The `sources` field records which inputs actually produced the note, so a wrong summary is attributable instead of a mystery.

## How it works

```
URL ─► detect platform
      ├─ Instagram: GET /p/<code>/embed/captioned/  ──► caption, creator, duration   (no login, no key)
      └─ any:       yt-dlp bestaudio ─────────────────► description + .m4a audio    (no ffmpeg)
                        │
                        ▼
              transcribe: OpenAI API  |  local faster-whisper  |  skip
                        │
                        ▼
              summarize:  OpenAI API  |  Anthropic API  |  claude -p  |  codex exec  |  skip
                        │
                        ▼
              reels/<date>-<creator>-<slug>.md
```

Three non-obvious things the code encodes, so you don't have to rediscover them:

1. **Instagram's captioned-embed page only renders its payload when the request carries `Sec-Fetch-Mode: navigate`.** Without it you get a 600 KB JavaScript shell and a 200 that means nothing. Invalid shortcodes are *also* a 200. The code checks the payload, never the status.
2. **yt-dlp is deliberately unpinned.** Its Instagram extractor breaks and is fixed in point releases; pinning is how other importers rotted within weeks. If Instagram fetches start failing, `pip install -U yt-dlp` first.
3. **Never give yt-dlp a custom User-Agent.** It pairs the UA with the rest of its browser fingerprint and Instagram rejects mismatches.

Every stage degrades independently: no transcription backend → caption-only note, LLM error → caption-derived title, embed blocked → yt-dlp description. You only get a hard failure when *nothing* could be fetched.

## Use it from Claude Code

Two optional integrations ship in the repo:

- **`/reel <url>` slash command** — `.claude/skills/reel/SKILL.md`. Clone the repo (or copy that folder into your own project's `.claude/skills/`) and Claude Code runs the pipeline and reports the takeaways in chat.
- **MCP server** — `.mcp.json` registers `reelnotes mcp` (stdio). Any MCP client (Claude Code, Claude Desktop) then gets an `import_reel_note(url)` tool. Install with the `mcp` extra.

Both are thin wrappers over the same `import_reel()` function, so you can also just:

```python
import asyncio
from reelnotes import import_reel

result = asyncio.run(import_reel("https://youtube.com/shorts/dQw4w9WgXcQ"))
print(result.path, result.takeaways)
```

## Limitations

- Public posts only. Private accounts, age-gated and login-walled content won't fetch.
- No OCR of on-screen text. Overlay-only recipes with no caption and no narration produce a thin note.
- Music-only clips download fine but transcribe to nothing; the note says so in its warnings.
- Local Whisper (`base` on CPU) is noticeably weaker than the OpenAI backend on music-backed or non-English speech and will return an empty transcript rather than guess. Try `REELNOTES_WHISPER_MODEL=small` or `medium`, or set `OPENAI_API_KEY`.
- Instagram rate-limits datacenter IPs more aggressively than residential ones. If you run this on a server and get empty embeds, that's why.
- One video at a time is the intended use. This is a personal-notes tool, not a scraper; respect the platforms' terms and creators' work.

## Development

```bash
git clone https://github.com/adenjonah/reel-notes && cd reel-notes
python -m venv .venv && . .venv/bin/activate
pip install -e ".[all,local,dev]"
pytest -q && ruff check src tests
```

Tests never touch the network or spawn a CLI — fetchers, API clients and subprocesses are patched.

## License

MIT
