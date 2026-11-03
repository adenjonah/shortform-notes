"""TikTok photo slideshows (``/@user/photo/<id>``).

yt-dlp answers a ``/photo/`` URL with "Unsupported URL", but the same post id under
``/video/`` gives the caption, creator and the slideshow's soundtrack (an mp3, no
picture). The slide images come from gallery-dl's TikTok extractor, which reads them
out of the page's rehydration JSON; we keep only its image URLs and fetch the bytes
ourselves, the same way Instagram carousel slides are fetched.

Short links (``vm.``/``vt.tiktok.com/<code>``, ``tiktok.com/t/<code>``) are resolved
first: only the redirect target says whether the post is a video or a slideshow.
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx

logger = logging.getLogger(__name__)

_SHORT_LINK_RE = re.compile(r"^https?://(?:(?:vm|vt)\.tiktok\.com/|(?:www\.)?tiktok\.com/t/)", re.I)
_PHOTO_RE = re.compile(r"tiktok\.com/@([\w.\-]*)/photo/(\d+)", re.I)
TIMEOUT = 15.0


def is_short_link(url: str) -> bool:
    return bool(_SHORT_LINK_RE.match(url))


def photo_post(url: str) -> tuple[str, str] | None:
    """``(user, post_id)`` when ``url`` is a photo slideshow, else None."""
    match = _PHOTO_RE.search(url)
    return (match.group(1), match.group(2)) if match else None


def photo_url(user: str, post_id: str) -> str:
    return f"https://www.tiktok.com/@{user}/photo/{post_id}"


def video_url(user: str, post_id: str) -> str:
    return f"https://www.tiktok.com/@{user}/video/{post_id}"


async def resolve_short_link(url: str) -> str | None:
    """The ``Location`` a short link redirects to, or None when it does not redirect."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as http:
            resp = await http.get(url)
    except httpx.HTTPError as exc:
        logger.warning("tiktok short link %s could not be resolved: %s", url, exc)
        return None
    return resp.headers.get("location") if resp.is_redirect else None


def _photo_urls_sync(url: str) -> tuple[str, ...]:
    from gallery_dl import extractor  # lazy: imports every extractor module
    from gallery_dl.extractor.message import Message

    found = extractor.find(url)
    if found is None:
        return ()
    # The extractor also yields the soundtrack (type "audio"); yt-dlp already fetches that.
    return tuple(msg[1] for msg in found if msg[0] == Message.Url and (msg[2] or {}).get("type") == "image")


async def fetch_photo_urls(url: str) -> tuple[str, ...]:
    """CDN URLs of every slide, in order; empty when gallery-dl cannot read the post."""
    try:
        return await asyncio.to_thread(_photo_urls_sync, url)
    except Exception as exc:  # noqa: BLE001 (gallery-dl raises its own HttpError / NotFoundError family)
        logger.warning("gallery-dl could not read tiktok slides for %s: %s", url, exc)
        return ()
