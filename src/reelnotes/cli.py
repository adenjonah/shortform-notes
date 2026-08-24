"""``reelnotes <url> [<url> ...]``: the command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from reelnotes import __version__
from reelnotes.config import SUMMARY_PROVIDERS, TRANSCRIBE_PROVIDERS, load_settings
from reelnotes.pipeline import ReelImportError, import_reel


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reelnotes",
        description="Turn Instagram Reels, TikToks and YouTube Shorts into Markdown notes.",
        epilog=(
            "Without an API key, summaries run through `claude` (Claude Code) or `codex` if either is on your PATH. "
            "Env: OPENAI_API_KEY, ANTHROPIC_API_KEY, REELNOTES_DIR, REELNOTES_SUMMARY_PROVIDER, "
            "REELNOTES_TRANSCRIBE_PROVIDER, REELNOTES_AUDIENCE. See .env.example."
        ),
    )
    parser.add_argument(
        "urls",
        nargs="*",
        help="one or more reel / short-video links; or 'web' for the local setup UI, 'mcp' for the MCP server",
    )
    parser.add_argument("-o", "--out", help="output directory (default: ./reels or $REELNOTES_DIR)")
    parser.add_argument(
        "--summary",
        choices=[*SUMMARY_PROVIDERS, "auto"],
        help="summary backend: openai, anthropic, claude-code, codex, or none (default: auto)",
    )
    parser.add_argument(
        "--transcribe",
        choices=[*TRANSCRIBE_PROVIDERS, "auto"],
        help="openai (API key), local (offline faster-whisper), or none (default: auto)",
    )
    parser.add_argument("--no-transcript", action="store_true", help="alias for --transcribe none")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON instead of text")
    parser.add_argument("-v", "--verbose", action="store_true", help="show fetch/debug logs")
    parser.add_argument("--version", action="version", version=f"reelnotes {__version__}")
    return parser


def _print_result(result, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return
    print(f"saved {result.path}  (sources: {', '.join(result.sources)})")
    print(f"  {result.title}")
    if result.summary:
        print(f"  {result.summary}")
    for t in result.takeaways:
        print(f"  - {t}")
    for w in result.warnings:
        print(f"  warning: {w}")


async def _run(urls: list[str], args: argparse.Namespace) -> int:
    settings = load_settings(
        output_dir=args.out,
        summary_provider=args.summary,
        transcribe_provider="none" if args.no_transcript else args.transcribe,
    )
    failures = 0
    for url in urls:
        try:
            _print_result(await import_reel(url, settings), args.json)
        except ReelImportError as exc:
            failures += 1
            if args.json:
                print(json.dumps({"url": url, "error": str(exc)}))
            else:
                print(f"failed {url}\n  {exc}", file=sys.stderr)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    if args.urls and args.urls[0] == "web":  # `reelnotes web [port]`: local setup and import UI
        from reelnotes.web.server import serve

        serve(port=int(args.urls[1]) if len(args.urls) > 1 else None, open_browser=not args.json)
        return 0
    if args.urls == ["mcp"]:  # `reelnotes mcp`: stdio MCP server for Claude Code / Claude Desktop
        from reelnotes.mcp_server import run_server

        run_server()
        return 0
    if not args.urls:
        parser.print_help()
        return 2
    return asyncio.run(_run(args.urls, args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
