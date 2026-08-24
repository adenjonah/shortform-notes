"""Optional MCP server so Claude Code / Claude Desktop can call ``import_reel`` as a tool.

Install with ``pip install "reelnotes[mcp]"`` and run ``reelnotes mcp``.
"""

from __future__ import annotations

import json

from reelnotes.config import load_settings
from reelnotes.pipeline import ReelImportError, import_reel


def build_server():
    # Lazy: optional dependency. mcp 2.x renamed FastMCP to MCPServer; support both.
    try:
        from mcp.server.mcpserver import MCPServer as _Server
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as _Server

    server = _Server("reelnotes")

    @server.tool()
    async def import_reel_note(url: str) -> str:
        """Import an Instagram reel/post, TikTok, or YouTube Short into a Markdown note.

        Fetches the caption, transcribes the audio (if OPENAI_API_KEY is set), writes
        <REELNOTES_DIR>/<date>-<creator>-<slug>.md and returns the title, summary,
        takeaways and note path as JSON. Call it when the user shares such a link.
        """
        try:
            result = await import_reel(url, load_settings())
        except ReelImportError as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(result.to_dict(), ensure_ascii=False)

    return server


def run_server() -> None:
    build_server().run()
