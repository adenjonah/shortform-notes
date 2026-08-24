# reelnotes

Turn Instagram Reels, TikToks and YouTube Shorts into Markdown notes with one command. Each note contains the caption, the transcript, and a model-written summary.

```
$ reelnotes https://www.instagram.com/reel/DQCkNLtgqEe/

saved reels/2025-10-20-teddysphotos-symmetry-with-karan-aujla.md  (sources: caption, transcript)
  Symmetry with Karan Aujla on Play Remixes EP
  The creator promotes the release of the track Symmetry featuring Karan Aujla ...
  - The song Symmetry features Karan Aujla (@karanaujla).
  - Karan Aujla taught the creator Punjabi for the video's bit.
```

An API key is optional. If [Claude Code](https://claude.com/claude-code) or [Codex CLI](https://developers.openai.com/codex/cli) is installed, summaries run through that subscription. With the `[local]` extra, transcripts run on your CPU with faster-whisper. With an OpenAI or Anthropic key, both steps run through the API at about $0.004 per reel.

The intended use is saving a recipe or tip you would otherwise lose in a bookmarks list. Notes are plain Markdown with YAML frontmatter and work in Obsidian, Logseq, or any folder.

## Quick start

```bash
git clone https://github.com/adenjonah/reel-notes && cd reel-notes && ./start.sh
```

`start.sh` installs [uv](https://docs.astral.sh/uv/) if it is missing, uv installs Python and the dependencies, and a setup page opens in your browser on 127.0.0.1. The page asks three questions: which backend writes the summary (Claude Code, Codex, or an API key), how to transcribe, and where to save notes. It then accepts a link and runs one import. The page does not scan your machine; it only reads its own config file.

Windows: run `start.ps1`. macOS without a terminal: double-click `Start.command`. Later runs skip setup and open the import page directly. `./start.sh <url>` imports from the terminal without opening the browser.

### Install as a CLI instead

```bash
pipx install "reelnotes[all] @ git+https://github.com/adenjonah/reel-notes"
```

Requires Python 3.10+. ffmpeg is not needed. Extras:

| Extra | Adds | Use it when |
|---|---|---|
| *(none)* | caption + metadata, Claude Code / Codex summaries | you want to run without any API key |
| `openai` | API transcription + summaries | you have `OPENAI_API_KEY` |
| `anthropic` | Claude API summaries | you have `ANTHROPIC_API_KEY` |
| `local` | offline Whisper transcription (faster-whisper, ~75 MB model on first run) | you want transcripts without a key |
| `mcp` | MCP server for Claude Code / Claude Desktop | you want it as an editor tool |
| `all` | `openai` + `anthropic` + `mcp` | |

## Backends for the two model steps

Two steps need a model: **transcribing** the audio and **summarizing** the text. Each step has several backends. `reelnotes` picks the first available one in the order below; configuration is only needed to override that choice.

```
summary     OPENAI_API_KEY, then ANTHROPIC_API_KEY, then `claude` on PATH, then `codex` on PATH, else none
transcript  OPENAI_API_KEY, then faster-whisper if installed, else none
```

### Option A: your own API key

```bash
export OPENAI_API_KEY=sk-...          # transcription (gpt-4o-mini-transcribe) + summaries (gpt-4o-mini)
export ANTHROPIC_API_KEY=sk-ant-...   # summaries with Claude instead (claude-opus-5)
reelnotes <url>
```

### Option B: Claude Code or Codex (no key)

If `claude` or `codex` is on your PATH and logged in, no further setup is needed:

```bash
reelnotes <url>                        # auto-detects
reelnotes --summary claude-code <url>  # or pin one
reelnotes --summary codex <url>
```

Each reel is one subprocess call with the prompt on stdin:

- Claude Code: `claude -p --output-format json --tools "" --disable-slash-commands --no-session-persistence`
- Codex: `codex exec --sandbox read-only --ask-for-approval never --output-last-message <tmp> -`

All tools are disabled and no session is persisted. The agent receives only the caption and transcript. Set `REELNOTES_CLAUDE_CODE_MODEL` / `REELNOTES_CODEX_MODEL` to pick a model; otherwise the CLI's own default is used.

### Option C: fully offline transcript

```bash
pip install "reelnotes[local]"
reelnotes --transcribe local <url>     # auto-detected once faster-whisper is installed and no OPENAI_API_KEY is set
```

`REELNOTES_WHISPER_MODEL` picks the model size (`tiny`, `base`, `small`, `medium`, `large-v3`; default `base`). Combined with Option B, no API key is used at any step.

### Configuration reference

The setup page writes `~/.config/reelnotes/config.env`; the CLI, MCP server and Claude Code skill all read it. Real environment variables override it, and flags override both:

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

`reelnotes --help` lists every option. `--json` gives machine-readable output; `--no-transcript` skips the audio download.

## Supported links

| Platform | URL shapes | Caption source | Notes |
|---|---|---|---|
| Instagram | `/reel/<code>`, `/reels/<code>`, `/p/<code>`, `/tv/<code>`, `/share/reel/<code>` | captioned-embed endpoint (no login) | Share links are resolved to the canonical shortcode; `?igsh=` tracking is stripped |
| TikTok | `tiktok.com/@user/video/<id>`, `vm.tiktok.com/<code>`, `vt.tiktok.com/<code>` | yt-dlp description | Short links are followed by yt-dlp |
| YouTube Shorts | `youtube.com/shorts/<id>`, `youtu.be/<id>` | yt-dlp description | Regular `watch?v=` URLs are intentionally not matched; this is a short-video tool |

Several links can be passed in one command; each becomes its own note.

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

**Source:** [instagram, @teddysphotos](https://www.instagram.com/reel/DQCkNLtgqEe/)

## Summary
...

## Key takeaways
- ...

## Caption
> verbatim caption

## Transcript
verbatim transcript
```

The `sources` field records which inputs actually produced the note, so a wrong summary can be traced to the input that produced it.

## How it works

```
URL -> detect platform
      +- Instagram: GET /p/<code>/embed/captioned/ -> caption, creator, duration   (no login, no key)
      +- any:       yt-dlp bestaudio -> description + .m4a audio                   (no ffmpeg)
                        |
                        v
              transcribe: OpenAI API  |  local faster-whisper  |  skip
                        |
                        v
              summarize:  OpenAI API  |  Anthropic API  |  claude -p  |  codex exec  |  skip
                        |
                        v
              reels/<date>-<creator>-<slug>.md
```

Three behaviors that are not obvious from the code:

1. **Instagram's captioned-embed page only renders its payload when the request carries `Sec-Fetch-Mode: navigate`.** Without it the response is a 600 KB JavaScript shell with status 200. Invalid shortcodes also return 200. The code checks for the `contextJSON` payload and ignores the status code.
2. **yt-dlp is deliberately unpinned.** Its Instagram extractor breaks and is fixed in point releases; pinned versions stop working within weeks. If Instagram fetches start failing, run `pip install -U yt-dlp` first.
3. **Do not give yt-dlp a custom User-Agent.** It pairs the UA with the rest of its browser fingerprint and Instagram rejects mismatches.

Every stage degrades independently: with no transcription backend the note is caption-only; on an LLM error the title is derived from the caption; if the embed is blocked the yt-dlp description is used. The import fails only when neither a caption nor audio could be fetched.

## Use it from Claude Code

Two optional integrations ship in the repo:

- **`/reel <url>` slash command**: `.claude/skills/reel/SKILL.md`. Clone the repo (or copy that folder into your own project's `.claude/skills/`) and Claude Code runs the pipeline and reports the takeaways in chat.
- **MCP server**: `.mcp.json` registers `reelnotes mcp` (stdio). Any MCP client (Claude Code, Claude Desktop) then gets an `import_reel_note(url)` tool. Install with the `mcp` extra.

Both call the same `import_reel()` function, which can also be used directly:

```python
import asyncio
from reelnotes import import_reel

result = asyncio.run(import_reel("https://youtube.com/shorts/dQw4w9WgXcQ"))
print(result.path, result.takeaways)
```

## Limitations

- Public posts only. Private accounts, age-gated and login-walled content cannot be fetched.
- No OCR of on-screen text. Overlay-only recipes with no caption and no narration produce a thin note.
- Music-only clips download but transcribe to nothing; the note records this in its warnings.
- Local Whisper (`base` on CPU) is weaker than the OpenAI backend on music-backed or non-English speech and returns an empty transcript rather than a guess. Try `REELNOTES_WHISPER_MODEL=small` or `medium`, or set `OPENAI_API_KEY`.
- Instagram rate-limits datacenter IPs more aggressively than residential ones. Empty embeds on a server are usually this rate limit.
- One video at a time is the intended use. It is a personal-notes tool. Bulk scraping is out of scope; respect the platforms' terms and creators' work.

## Development

```bash
git clone https://github.com/adenjonah/reel-notes && cd reel-notes
python -m venv .venv && . .venv/bin/activate
pip install -e ".[all,local,dev]"
pytest -q && ruff check src tests
```

Tests do not touch the network or spawn a CLI: fetchers, API clients and subprocesses are patched.

## License

MIT
