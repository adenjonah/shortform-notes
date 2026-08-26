"""Instagram caption fetch via the public captioned-embed page. No login, no API key.

``https://www.instagram.com/p/<shortcode>/embed/captioned/`` server-renders a
``contextJSON`` payload (caption, owner, CDN video URL, duration, and for carousels
the slide image URLs under ``edge_sidecar_to_children``) **only** when
the request carries ``Sec-Fetch-Mode: navigate``. Without that header you get a
~600 KB JavaScript shell and an HTTP 200 that means nothing. Invalid shortcodes
also return 200, so always check the payload, never the status code.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from html import unescape

import httpx

logger = logging.getLogger(__name__)

_SHORTCODE_RE = re.compile(r"instagram\.com/(?:reels?|p|tv)/([A-Za-z0-9_\-]+)", re.I)
_SHARE_RE = re.compile(r"instagram\.com/share/", re.I)
_CONTEXT_JSON_RE = re.compile(r'"contextJSON":"((?:[^"\\]|\\.)*)"')
_CAPTION_DIV_RE = re.compile(r'<div class="Caption">(.*?)</div>\s*</div>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")

# A real desktop UA plus the navigate hint, the combination Instagram renders for.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Sec-Fetch-Mode": "navigate",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 15.0


@dataclass(frozen=True)
class InstagramEmbed:
    shortcode: str
    caption: str | None
    username: str | None
    video_url: str | None
    thumbnail: str | None
    duration: float | None
    is_video: bool
    # Carousel / single-image posts: full-size CDN URLs of every image slide, in order.
    # Empty for videos. These are what --vision looks at when yt-dlp has no video to fetch.
    image_urls: tuple[str, ...] = ()


def _image_urls(media: dict) -> tuple[str, ...]:
    """Image slides of a carousel (``edge_sidecar_to_children``), or the one image of a photo post."""
    edges = (media.get("edge_sidecar_to_children") or {}).get("edges") or []
    children = [edge.get("node") or {} for edge in edges] or [media]
    return tuple(child["display_url"] for child in children if child.get("display_url") and not child.get("is_video"))


async def resolve_shortcode(url: str) -> str | None:
    """Return the shortcode, following ``/share/<token>`` redirects if needed."""
    direct = _SHORTCODE_RE.search(url)
    if direct:
        return direct.group(1)
    if not _SHARE_RE.search(url):
        return None
    # Share tokens are opaque; Instagram 302s them to the canonical /reel/<code>/
    # only when the request looks like a navigation.
    async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=False) as http:
        resp = await http.get(url)
    match = _SHORTCODE_RE.search(resp.headers.get("location", ""))
    return match.group(1) if match else None


def parse_embed(html: str, shortcode: str) -> InstagramEmbed | None:
    """Parse the captioned-embed HTML. None means Instagram served the shell only."""
    ctx_match = _CONTEXT_JSON_RE.search(html)
    if ctx_match:
        try:
            # contextJSON is a JSON string literal whose value is itself JSON.
            context = json.loads(json.loads('"' + ctx_match.group(1) + '"'))
            media = context.get("gql_data", {}).get("shortcode_media") or {}
        except (ValueError, AttributeError):
            media = {}
        if media:
            edges = media.get("edge_media_to_caption", {}).get("edges") or []
            caption = edges[0].get("node", {}).get("text") if edges else None
            return InstagramEmbed(
                shortcode=media.get("shortcode") or shortcode,
                caption=caption or None,
                username=(media.get("owner") or {}).get("username"),
                video_url=media.get("video_url"),
                thumbnail=media.get("thumbnail_src") or media.get("display_url"),
                duration=media.get("video_duration"),
                is_video=bool(media.get("is_video")),
                image_urls=_image_urls(media),
            )
    # Older posts render the caption as a plain div instead of contextJSON.
    div_match = _CAPTION_DIV_RE.search(html)
    if div_match:
        text = unescape(_TAG_RE.sub("", div_match.group(1).replace("<br>", "\n"))).strip()
        return InstagramEmbed(shortcode, text or None, None, None, None, None, False, ())
    return None


async def fetch_embed(shortcode: str) -> InstagramEmbed | None:
    """GET the captioned embed; None when blocked/deleted (never raises on a 200 shell)."""
    embed_url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as http:
            resp = await http.get(embed_url)
    except httpx.HTTPError as exc:
        logger.warning("instagram embed fetch failed for %s: %s", shortcode, exc)
        return None
    if resp.status_code != 200:
        logger.warning("instagram embed %s returned HTTP %s", shortcode, resp.status_code)
        return None
    parsed = parse_embed(resp.text, shortcode)
    if parsed is None:
        logger.warning("instagram embed %s: shell only (%d bytes), no payload", shortcode, len(resp.text))
    return parsed
