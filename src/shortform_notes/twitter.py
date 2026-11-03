"""X / Twitter posts via the public syndication endpoint (the one embedded-tweet widgets use). No login.

``cdn.syndication.twimg.com/tweet-result?id=<id>&token=<t>`` returns the post's text,
author, timestamp and ``mediaDetails`` (photos, videos, GIFs). yt-dlp uses the same
endpoint as its fallback, but only to find a video: on a photo or text post it raises
"No video could be found in this tweet". So this module decides what kind of post it
is, and yt-dlp is only asked for the video when there is one.

Limits of the public payload: a long post ("note tweet") arrives cut to its first ~280
characters, and protected or deleted posts return an empty body or a tombstone.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

SYNDICATION_URL = "https://cdn.syndication.twimg.com/tweet-result"
TIMEOUT = 15.0
_STATUS_RE = re.compile(r"(?:twitter|x)\.com/(i/web|i|[A-Za-z0-9_]{1,15})/status(?:es)?/(\d+)", re.I)
_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"


@dataclass(frozen=True)
class XPost:
    id: str
    text: str | None
    username: str | None
    name: str | None
    timestamp: int | None
    image_urls: tuple[str, ...]
    has_video: bool  # a video or animated GIF: yt-dlp downloads it
    truncated: bool  # a long post whose full body the public payload does not carry


def canonical_url(url: str) -> str | None:
    """``https://x.com/<user>/status/<id>`` with tracking params and ``/photo/1`` suffixes dropped."""
    match = _STATUS_RE.search(url)
    if not match:
        return None
    user = "i" if match.group(1).lower() == "i/web" else match.group(1)
    return f"https://x.com/{user}/status/{match.group(2)}"


def status_id(url: str) -> str | None:
    match = _STATUS_RE.search(url)
    return match.group(2) if match else None


def _js_radix_string(value: float, radix: int) -> str:
    """JavaScript's ``Number.prototype.toString(radix)`` for a positive float (V8's shortest-digits loop)."""
    integer, fraction = int(value), value - int(value)
    delta = max(0.5 * (math.nextafter(value, math.inf) - value), math.nextafter(0.0, 1.0))
    digits: list[int] = []
    while fraction >= delta:
        fraction *= radix
        delta *= radix
        digit = int(fraction)
        digits.append(digit)
        fraction -= digit
        if (fraction > 0.5 or (fraction == 0.5 and digit & 1)) and fraction + delta > 1:
            # Round up and carry; a carry out of the fraction bumps the integer part.
            while digits and digits[-1] + 1 == radix:
                digits.pop()
            if digits:
                digits[-1] += 1
            else:
                integer += 1
            break
    head = ""
    while integer:
        integer, rem = divmod(integer, radix)
        head = _DIGITS[rem] + head
    head = head or "0"
    return head + ("." + "".join(_DIGITS[d] for d in digits) if digits else "")


def syndication_token(tweet_id: str) -> str:
    """``((id / 1e15) * Math.PI).toString(36).replace(/(0+|\\.)/g, '')``, as the embed widget sends it."""
    return re.sub(r"(0+|\.)", "", _js_radix_string(int(tweet_id) / 1e15 * math.pi, 36))


def _timestamp(created_at: str | None) -> int | None:
    if not created_at:
        return None
    try:
        return int(datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def parse_status(data: dict | None) -> XPost | None:
    """The syndication JSON as an XPost; None for an empty body or a tombstone (deleted / protected)."""
    if not data or data.get("__typename") not in (None, "Tweet") or not data.get("id_str"):
        return None
    text = data.get("text") or ""
    span = data.get("display_text_range")
    if isinstance(span, list) and len(span) == 2:
        text = text[span[0] : span[1]]  # drops the trailing t.co link that points at the attached media
    media = [m for m in data.get("mediaDetails") or [] if isinstance(m, dict)]
    user = data.get("user") or {}
    return XPost(
        id=data["id_str"],
        text=text.strip() or None,
        username=user.get("screen_name"),
        name=user.get("name"),
        timestamp=_timestamp(data.get("created_at")),
        image_urls=tuple(m["media_url_https"] for m in media if m.get("type") == "photo" and m.get("media_url_https")),
        has_video=any(m.get("type") in ("video", "animated_gif") for m in media),
        truncated=bool(data.get("note_tweet")),
    )


async def fetch_status(tweet_id: str) -> dict | None:
    """GET the syndication JSON; None when the request fails or the post is not public."""
    params = {"id": tweet_id, "token": syndication_token(tweet_id), "lang": "en"}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as http:
            resp = await http.get(SYNDICATION_URL, params=params)
    except httpx.HTTPError as exc:
        logger.warning("x syndication fetch failed for %s: %s", tweet_id, exc)
        return None
    if resp.status_code != 200 or not resp.content:
        logger.warning("x syndication for %s returned HTTP %s", tweet_id, resp.status_code)
        return None
    try:
        return resp.json()
    except ValueError:
        return None
