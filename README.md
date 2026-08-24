# reelnotes

Turn Instagram Reels, TikToks and YouTube Shorts into Markdown notes — caption, transcript, and an AI summary — with one command.

```
$ reelnotes https://www.instagram.com/reel/DQCkNLtgqEe/

✓ reels/2026-08-12-teddysphotos-symmetry-out-now.md  (sources: caption, transcript)
  Symmetry ft. Karan Aujla out now
  Ed Sheeran announces the release of "Symmetry", a collaboration with Karan Aujla ...
  • Track title: Symmetry
  • Collaborator: @karanaujla
```

Works out of the box with **no API keys** (you get the caption + metadata). Add `OPENAI_API_KEY` and you also get a verbatim transcript and a summary with concrete takeaways. Costs about **$0.004 per reel**.

Built for "I'll save this recipe for later" and then never opening Instagram again. Notes are plain Markdown with YAML frontmatter, so they drop straight into Obsidian, Logseq, or a folder.

## Install

```bash
pipx install "reelnotes[all] @ git+https://github.com/adenjonah/reel-notes"
# or: pip install "reelnotes[all] @ git+https://github.com/adenjonah/reel-notes"
```

Requires Python 3.10+. No ffmpeg needed. Drop the `[all]` extra for the key-free caption mode only.

## Use

```bash
# Caption + metadata note, no keys needed
reelnotes https://www.tiktok.com/@chef/video/7301234567890123456

# Full pipeline: caption + transcript + summary
export OPENAI_API_KEY=sk-...
reelnotes https://www.instagram.com/reel/DQCkNLtgqEe/ https://youtube.com/shorts/dQw4w9WgXcQ

# Into your vault, summarized by Claude, addressed to you
export ANTHROPIC_API_KEY=sk-ant-...
reelnotes -o ~/notes/reels --summary anthropic <url>
```

`reelnotes --help` lists everything. `--json` gives machine-readable output; `--no-transcript` skips the audio download.

### Configuration

Everything is an environment variable (copy `.env.example`):

| Variable | Default | What it does |
|---|---|---|
| `OPENAI_API_KEY` | — | Enables transcription (`gpt-4o-mini-transcribe`) and OpenAI summaries |
| `ANTHROPIC_API_KEY` | — | Enables Claude summaries (`claude-opus-5`) |
| `REELNOTES_DIR` | `reels` | Output directory |
| `REELNOTES_SUMMARY_PROVIDER` | auto | `openai`, `anthropic` or `none` |
| `REELNOTES_AUDIENCE` | `the reader` | Who the summary is written for, e.g. `Jonah, a home cook` |
| `REELNOTES_TRANSCRIBE` | `1` | Set `0` to never download audio |

## What a note looks like

```markdown
---
type: reel
platform: instagram
source: https://www.instagram.com/reel/DQCkNLtgqEe/
creator: "@teddysphotos"
posted: 2026-08-12
imported: 2026-08-23
duration_seconds: 26
sources: [caption, transcript]
tags: [reel]
---

# Symmetry ft. Karan Aujla out now

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
              OpenAI transcription ──► verbatim transcript            (needs OPENAI_API_KEY)
                        │
                        ▼
              one LLM call (OpenAI or Claude, strict JSON schema) ──► title, summary, takeaways
                        │
                        ▼
              reels/<date>-<creator>-<slug>.md
```

Three non-obvious things the code encodes, so you don't have to rediscover them:

1. **Instagram's captioned-embed page only renders its payload when the request carries `Sec-Fetch-Mode: navigate`.** Without it you get a 600 KB JavaScript shell and a 200 that means nothing. Invalid shortcodes are *also* a 200. The code checks the payload, never the status.
2. **yt-dlp is deliberately unpinned.** Its Instagram extractor breaks and is fixed in point releases; pinning is how other importers rotted within weeks. If Instagram fetches start failing, `pip install -U yt-dlp` first.
3. **Never give yt-dlp a custom User-Agent.** It pairs the UA with the rest of its browser fingerprint and Instagram rejects mismatches.

Every stage degrades independently: no key → no transcript, LLM error → caption-derived title, embed blocked → yt-dlp description. You only get a hard failure when *nothing* could be fetched.

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
- Instagram rate-limits datacenter IPs more aggressively than residential ones. If you run this on a server and get empty embeds, that's why.
- One video at a time is the intended use. This is a personal-notes tool, not a scraper; respect the platforms' terms and creators' work.

## Development

```bash
git clone https://github.com/adenjonah/reel-notes && cd reel-notes
python -m venv .venv && . .venv/bin/activate
pip install -e ".[all,dev]"
pytest -q && ruff check src tests
```

Tests never touch the network — fetchers and LLM calls are patched.

## License

MIT
