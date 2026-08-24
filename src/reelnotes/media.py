"""Audio + metadata download via yt-dlp.

``bestaudio/best`` and no ffmpeg: Instagram and YouTube expose a standalone
audio stream, TikTok falls through to the muxed mp4, and transcription APIs
accept both. Never set a custom User-Agent — yt-dlp pairs its UA with the rest
of the browser fingerprint and Instagram rejects mismatches.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# OpenAI's transcription upload cap; yt-dlp aborts the download past this.
MAX_AUDIO_BYTES = 25 * 1024 * 1024


class MediaFetchError(Exception):
    """yt-dlp could not fetch the video; the caller should surface, not retry."""


@dataclass(frozen=True)
class DownloadedMedia:
    audio_path: str | None
    caption: str | None
    creator_handle: str | None
    creator_name: str | None
    title: str | None
    timestamp: int | None
    duration: float | None
    thumbnail: str | None
    webpage_url: str | None
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _ytdlp_sync(url: str, tmpdir: str, download: bool) -> DownloadedMedia:
    import yt_dlp  # lazy: heavy import, and tests stub this function

    warnings: list[str] = []
    opts = {
        "format": "bestaudio/best",
        "outtmpl": f"{tmpdir}/%(id)s.%(ext)s",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "max_filesize": MAX_AUDIO_BYTES,
        "socket_timeout": 20,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=download)
    downloads = info.get("requested_downloads") or []
    audio_path = downloads[0].get("filepath") if downloads else None
    if download and audio_path is None:
        warnings.append("yt-dlp returned metadata but no downloadable audio stream")
    title = info.get("title") or ""
    return DownloadedMedia(
        audio_path=audio_path,
        caption=(info.get("description") or "").strip() or None,
        creator_handle=info.get("channel") or info.get("uploader_id"),
        creator_name=info.get("uploader") or info.get("channel"),
        # yt-dlp synthesises "Video by <user>" for Instagram — not real content.
        title=None if title.startswith("Video by ") else (title or None),
        timestamp=info.get("timestamp"),
        duration=info.get("duration"),
        thumbnail=info.get("thumbnail"),
        webpage_url=info.get("webpage_url") or url,
        warnings=tuple(warnings),
    )


async def download_media(url: str, tmpdir: str, download: bool = True) -> DownloadedMedia:
    """Run yt-dlp off the event loop. ``download=False`` fetches metadata only."""
    try:
        return await asyncio.to_thread(_ytdlp_sync, url, tmpdir, download)
    except Exception as exc:  # yt-dlp raises DownloadError and friends
        message = str(exc).splitlines()[0][:300]
        logger.warning("yt-dlp failed for %s: %s", url, message)
        raise MediaFetchError(message) from exc
