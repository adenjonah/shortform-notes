"""Render the Markdown note (Obsidian-friendly YAML frontmatter + sections)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime

_SLUG_MAX = 48


@dataclass(frozen=True)
class ReelContent:
    url: str
    platform: str
    caption: str | None
    transcript: str | None
    title: str | None  # platform-provided title (YouTube/TikTok); Instagram has none
    creator_handle: str | None
    creator_name: str | None
    posted: datetime | None
    duration: float | None
    thumbnail: str | None
    sources: tuple[str, ...]
    warnings: tuple[str, ...]


def slugify(text: str, max_len: int = _SLUG_MAX) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:max_len].rstrip("-") or "reel"


def note_filename(date: datetime, creator_handle: str | None, title: str) -> str:
    handle = slugify(creator_handle or "", 24) if creator_handle else ""
    parts = [date.strftime("%Y-%m-%d"), handle, slugify(title)]
    return "-".join(p for p in parts if p) + ".md"


def _yaml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _blockquote(text: str) -> str:
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.strip().splitlines())


def build_note(
    content: ReelContent,
    title: str,
    summary: str,
    takeaways: tuple[str, ...],
    imported_at: datetime,
) -> str:
    """Frontmatter, summary, takeaways, then the verbatim caption and transcript.

    ``sources`` records which inputs actually produced the note (caption, transcript)
    so a wrong summary is attributable to its input rather than a mystery.
    """
    front = [
        "---",
        "type: reel",
        f"platform: {content.platform}",
        f"source: {content.url}",
        f"creator: {_yaml_str('@' + content.creator_handle) if content.creator_handle else 'null'}",
        f"creator_name: {_yaml_str(content.creator_name) if content.creator_name else 'null'}",
        f"posted: {content.posted.strftime('%Y-%m-%d') if content.posted else 'null'}",
        f"imported: {imported_at.strftime('%Y-%m-%d')}",
        f"duration_seconds: {round(content.duration) if content.duration else 'null'}",
        f"sources: [{', '.join(content.sources)}]",
        "tags: [reel]",
        "---",
    ]
    creator = f"@{content.creator_handle}" if content.creator_handle else (content.creator_name or "unknown creator")
    body = [f"# {title}", "", f"**Source:** [{content.platform}, {creator}]({content.url})"]
    if content.thumbnail:
        body += ["", f"![thumbnail]({content.thumbnail})"]
    body += ["", "## Summary", "", summary or "_No summary generated._"]
    if takeaways:
        body += ["", "## Key takeaways", ""] + [f"- {t}" for t in takeaways]
    body += ["", "## Caption", ""]
    body += [_blockquote(content.caption)] if content.caption else ["_No caption._"]
    body += ["", "## Transcript", ""]
    body += [content.transcript] if content.transcript else ["_No transcript._"]
    if content.warnings:
        body += ["", "## Import warnings", ""] + [f"- {w}" for w in content.warnings]
    body += ["", f"*Imported by reelnotes on {imported_at.strftime('%Y-%m-%d %H:%M UTC')}*", ""]
    return "\n".join(front + [""] + body)
