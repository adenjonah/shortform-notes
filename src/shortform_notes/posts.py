"""Pre-fetch for posts yt-dlp cannot take as given: TikTok photo slideshows and X posts.

``prefetch`` runs before yt-dlp and says what yt-dlp should fetch (a rewritten URL, audio
only, or nothing at all) plus whatever caption, author and slide images the platform
exposes. Instagram has its own embed path in ``instagram.py``; plain TikTok videos and
YouTube Shorts need no pre-fetch and get None.
"""

from __future__ import annotations

from dataclasses import dataclass

from shortform_notes import tiktok, twitter


@dataclass(frozen=True)
class Post:
    url: str  # canonical URL: what the note links to and what dedup keys on
    media_url: str | None  # what yt-dlp fetches; None when the post has nothing for it (X photo/text post)
    video: bool = True  # False: ask yt-dlp for audio only (a slideshow's "best" stream is its soundtrack)
    caption: str | None = None
    username: str | None = None
    creator_name: str | None = None
    timestamp: int | None = None
    image_urls: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


async def _tiktok(url: str) -> Post | None:
    target = url
    if tiktok.is_short_link(url):
        target = await tiktok.resolve_short_link(url) or url
    parts = tiktok.photo_post(target)
    if parts is None:
        return None  # a video: yt-dlp handles the original link, short or not, as before
    clean = tiktok.photo_url(*parts)
    image_urls = await tiktok.fetch_photo_urls(clean)
    warnings = () if image_urls else ("TikTok slideshow images could not be read (gallery-dl returned none)",)
    return Post(clean, tiktok.video_url(*parts), video=False, image_urls=image_urls, warnings=warnings)


async def _x(url: str) -> Post | None:
    clean = twitter.canonical_url(url)
    tweet_id = twitter.status_id(url)
    if not clean or not tweet_id:
        return None
    post = twitter.parse_status(await twitter.fetch_status(tweet_id))
    if post is None:
        # Let yt-dlp try on its own (it can still find a video through its guest API).
        return Post(clean, clean, warnings=("X post metadata unavailable (deleted, protected or rate-limited)",))
    warnings = ("X long post: only its first 280 characters are public without login",) if post.truncated else ()
    return Post(
        clean,
        clean if post.has_video else None,
        caption=post.text,
        username=post.username,
        creator_name=post.name,
        timestamp=post.timestamp,
        image_urls=post.image_urls,
        warnings=warnings,
    )


async def prefetch(url: str, platform: str) -> Post | None:
    if platform == "tiktok":
        return await _tiktok(url)
    if platform == "x":
        return await _x(url)
    return None
