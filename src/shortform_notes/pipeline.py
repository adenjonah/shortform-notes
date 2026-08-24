"""Orchestrator: URL to caption and transcript, to summary, to a Markdown file on disk.

Each source is independent and the note records which ones succeeded:

  1. caption    Instagram: captioned-embed payload (no key). TikTok/YouTube:
                yt-dlp ``description``. Cheap and often the whole recipe.
  2. transcript yt-dlp ``bestaudio``, then OpenAI transcription or local faster-whisper.
  3. summary    one LLM call: OpenAI / Anthropic API, or the ``claude`` / ``codex``
                CLI using an existing subscription. Best-effort.

Typical cost with everything on: about $0.004 per minute-long reel.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from shortform_notes import instagram, media, ocr, urls
from shortform_notes.config import Settings, load_settings
from shortform_notes.note import ReelContent, build_note, note_filename
from shortform_notes.summarize import summarize
from shortform_notes.transcribe import transcribe

logger = logging.getLogger(__name__)


class ReelImportError(Exception):
    """Nothing usable could be fetched from the URL."""


@dataclass(frozen=True)
class ReelImportResult:
    path: Path
    title: str
    summary: str
    takeaways: tuple[str, ...]
    sources: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "title": self.title,
            "summary": self.summary,
            "takeaways": list(self.takeaways),
            "sources": list(self.sources),
            "warnings": list(self.warnings),
        }


async def gather_content(url: str, tmpdir: str, settings: Settings) -> ReelContent:
    """Collect caption and transcript from every source that works."""
    platform = urls.platform_for(url)
    clean_url = urls.strip_tracking(url)
    embed: instagram.InstagramEmbed | None = None
    downloaded: media.DownloadedMedia | None = None
    warnings: list[str] = []

    if platform == "instagram":
        shortcode = await instagram.resolve_shortcode(url)
        if shortcode:
            clean_url = f"https://www.instagram.com/reel/{shortcode}/"
            embed = await instagram.fetch_embed(shortcode)
            if embed is None:
                warnings.append("Instagram embed endpoint returned no payload (blocked, private or deleted)")
        else:
            warnings.append("Could not resolve an Instagram shortcode from the link")

    try:
        downloaded = await media.download_media(
            clean_url, tmpdir, download=settings.can_transcribe or settings.ocr, video=settings.ocr
        )
        warnings.extend(downloaded.warnings)
    except media.MediaFetchError as exc:
        warnings.append(f"yt-dlp could not fetch media: {exc}")

    caption = (embed.caption if embed else None) or (downloaded.caption if downloaded else None)
    transcript = None
    if downloaded and downloaded.audio_path and settings.can_transcribe:
        try:
            transcript = await transcribe(downloaded.audio_path, settings)
        except Exception as exc:  # noqa: BLE001 (surface as a warning, keep the caption)
            warnings.append(f"transcription failed: {exc}")
        if not transcript and "transcription failed" not in " ".join(warnings):
            warnings.append("Audio downloaded but transcription returned nothing")
    elif not settings.can_transcribe:
        warnings.append(
            'Transcription skipped (set OPENAI_API_KEY, or pip install "shortform-notes[local]" for offline Whisper)'
        )

    screen_text = None
    if settings.ocr and downloaded and downloaded.video_path:
        duration = (embed.duration if embed else None) or downloaded.duration or 0
        est = ocr.estimate(duration, settings)
        logger.info("OCR: %s", est.describe())
        try:
            screen_text, frames_read = await ocr.read_screen_text(downloaded.video_path, settings)
            if not screen_text:
                warnings.append(f"OCR read {frames_read} frames and found no on-screen text")
        except Exception as exc:  # noqa: BLE001 (surface as a warning, keep the rest of the note)
            warnings.append(f"OCR failed: {exc}")
    elif settings.ocr:
        warnings.append("OCR skipped: no video could be downloaded")

    sources = tuple(
        s for s, present in (("caption", caption), ("transcript", transcript), ("screen_text", screen_text)) if present
    )
    if not sources:
        raise ReelImportError("; ".join(warnings) or "no caption or audio available")

    posted = (
        datetime.fromtimestamp(downloaded.timestamp, tz=timezone.utc) if downloaded and downloaded.timestamp else None
    )
    return ReelContent(
        url=clean_url,
        platform=platform,
        caption=caption,
        transcript=transcript,
        screen_text=screen_text,
        title=downloaded.title if downloaded else None,
        creator_handle=(embed.username if embed else None) or (downloaded.creator_handle if downloaded else None),
        creator_name=downloaded.creator_name if downloaded else None,
        posted=posted,
        duration=(embed.duration if embed else None) or (downloaded.duration if downloaded else None),
        thumbnail=(embed.thumbnail if embed else None) or (downloaded.thumbnail if downloaded else None),
        sources=sources,
        warnings=tuple(warnings),
    )


def _unique_path(directory: Path, filename: str, now: datetime) -> Path:
    path = directory / filename
    if not path.exists():
        return path
    return directory / f"{filename[:-3]}-{now.strftime('%H%M%S')}.md"


async def import_reel(url: str, settings: Settings | None = None, now: datetime | None = None) -> ReelImportResult:
    """Fetch, transcribe, summarize and write ``<output_dir>/<date>-<creator>-<slug>.md``."""
    settings = settings or load_settings()
    now = now or datetime.now(timezone.utc)
    clean = urls.detect_reel_url(url)
    if not clean:
        raise ReelImportError(f"not a supported Instagram / TikTok / YouTube Shorts link: {url}")

    with tempfile.TemporaryDirectory(prefix="shortform-notes-") as tmpdir:
        content = await gather_content(clean, tmpdir, settings)
    result = await summarize(
        content.caption, content.transcript, settings, title_hint=content.title, screen_text=content.screen_text
    )

    settings.output_dir.mkdir(parents=True, exist_ok=True)
    path = _unique_path(
        settings.output_dir, note_filename(content.posted or now, content.creator_handle, result.title), now
    )
    path.write_text(build_note(content, result.title, result.summary, result.takeaways, now), encoding="utf-8")
    logger.info("reel imported: %s sources=%s", path, content.sources)
    return ReelImportResult(path, result.title, result.summary, result.takeaways, content.sources, content.warnings)
