"""URL detection and normalisation for supported platforms."""

from __future__ import annotations

import re
from urllib.parse import urlparse

# One regex, shared by the CLI, the MCP tool and the detector, so they never disagree.
REEL_URL_RE = re.compile(
    r"https?://(?:www\.|m\.)?(?:"
    r"instagram\.com/(?:reels?|p|tv|share/(?:reel|p|reels))/[A-Za-z0-9_\-]+"
    r"|(?:vm\.|vt\.)?tiktok\.com/[^\s<>\"']+"
    r"|youtube\.com/shorts/[A-Za-z0-9_\-]+"
    r"|youtu\.be/[A-Za-z0-9_\-]+"
    r")[^\s<>\"']*",
    re.I,
)


def detect_reel_url(text: str) -> str | None:
    """Return the first supported short-video URL in ``text``, else None."""
    match = REEL_URL_RE.search(text or "")
    return match.group(0).rstrip(".,;)") if match else None


def platform_for(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    return "unknown"


def strip_tracking(url: str) -> str:
    """Drop query/fragment (``?igsh=…``, ``?si=…``) so the same video dedups."""
    parsed = urlparse(url)
    return parsed._replace(query="", fragment="").geturl()
