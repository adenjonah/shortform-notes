"""``reelnotes <url> [<url> ...]`` — the command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from reelnotes import __version__
from reelnotes.config import load_settings
from reelnotes.pipeline import ReelImportError, import_reel


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reelnotes",
        description="Turn Instagram Reels, TikToks and YouTube Shorts into Markdown notes.",
        epilog=(
            "Environment: OPENAI_API_KEY (transcription + summary), ANTHROPIC_API_KEY (summary), "
            "REELNOTES_DIR (output dir), REELNOTES_SUMMARY_PROVIDER, REELNOTES_AUDIENCE. "
            "With no keys set you still get a caption + metadata note."
        ),
    )
    parser.add_argument(
        "urls", nargs="*", help="one or more reel / short-video links; or the word 'mcp' to run the MCP server"
    )
    parser.add_argument("-o", "--out", help="output directory (default: ./reels or $REELNOTES_DIR)")
    parser.add_argument("--summary", choices=["openai", "anthropic", "none"], help="summary provider (default: auto)")
    parser.add_argument("--no-transcript", action="store_true", help="skip audio download + transcription")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON instead of text")
    parser.add_argument("-v", "--verbose", action="store_true", help="show fetch/debug logs")
    parser.add_argument("--version", action="version", version=f"reelnotes {__version__}")
    return parser


def _print_result(result, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return
    print(f"✓ {result.path}  (sources: {', '.join(result.sources)})")
    print(f"  {result.title}")
    if result.summary:
        print(f"  {result.summary}")
    for t in result.takeaways:
        print(f"  • {t}")
    for w in result.warnings:
        print(f"  ! {w}")


async def _run(urls: list[str], args: argparse.Namespace) -> int:
    settings = load_settings(output_dir=args.out, summary_provider=args.summary, transcribe=not args.no_transcript)
    failures = 0
    for url in urls:
        try:
            _print_result(await import_reel(url, settings), args.json)
        except ReelImportError as exc:
            failures += 1
            if args.json:
                print(json.dumps({"url": url, "error": str(exc)}))
            else:
                print(f"✗ {url}\n  {exc}", file=sys.stderr)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    if args.urls == ["mcp"]:  # `reelnotes mcp` → stdio MCP server for Claude Code / Claude Desktop
        from reelnotes.mcp_server import run_server

        run_server()
        return 0
    if not args.urls:
        parser.print_help()
        return 2
    return asyncio.run(_run(args.urls, args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
