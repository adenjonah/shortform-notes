"""Orchestrator: URL to caption and transcript, to summary, to a Markdown file on disk.

Each source is independent and the note records which ones succeeded:

  1. caption    Instagram: captioned-embed payload (no key). TikTok/YouTube:
                yt-dlp ``description``. Cheap and often the whole recipe.
  2. transcript yt-dlp ``bestaudio``, then OpenAI transcription or local faster-whisper.
  3. summary    one LLM call: OpenAI / Anthropic API, or the ``claude`` / ``codex``
                CLI using an existing subscription. Best-effort. With ``vision``
                on, the sampled video frames ride along in that same call.

Typical cost with everything on: about $0.004 per minute-long reel.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from shortform_notes import instagram, media, ocr, urls
from shortform_notes.config import AGENTIC_VISION_PROVIDERS, Settings, load_settings
from shortform_notes.note import ReelContent, Scene, build_note, note_filename
from shortform_notes.summarize import summarize, vision_estimate
from shortform_notes.transcribe import transcribe

logger = logging.getLogger(__name__)


class ReelImportError(Exception):
    """Nothing usable could be fetched from the URL."""


SLIDE_FETCH_TIMEOUT = 20.0


async def fetch_slides(image_urls: tuple[str, ...]) -> tuple[list[bytes], list[str]]:
    """Download carousel slides from Instagram's CDN, in order. A failed slide is a warning, not a failure."""
    images: list[bytes] = []
    warnings: list[str] = []
    async with httpx.AsyncClient(headers=instagram.HEADERS, timeout=SLIDE_FETCH_TIMEOUT, follow_redirects=True) as http:
        for index, image_url in enumerate(image_urls, start=1):
            try:
                resp = await http.get(image_url)
                resp.raise_for_status()
                images.append(resp.content)
            except httpx.HTTPError as exc:
                warnings.append(f"slide {index} could not be fetched: {exc}")
    return images, warnings


@dataclass(frozen=True)
class ReelImportResult:
    path: Path
    title: str
    summary: str
    takeaways: tuple[str, ...]
    sources: tuple[str, ...]
    warnings: tuple[str, ...]
    scenes: tuple[Scene, ...] = ()  # only under --vision; see summarize.summary_schema

    def to_dict(self) -> dict:
        data = {
            "path": str(self.path),
            "title": self.title,
            "summary": self.summary,
            "takeaways": list(self.takeaways),
            "sources": list(self.sources),
            "warnings": list(self.warnings),
        }
        if self.scenes:  # absent, not empty, so a run without vision looks exactly as it did
            data["scenes"] = [scene.to_dict() for scene in self.scenes]
        return data


async def gather_content(url: str, tmpdir: str, settings: Settings) -> tuple[ReelContent, list[ocr.Frame]]:
    """Collect caption, transcript and (for OCR or vision) sampled video frames from every source that works.

    The frames come back alongside the note content because they are an input to
    the summary call, not something the note itself renders.
    """
    platform = urls.platform_for(url)
    clean_url = urls.strip_tracking(url)
    embed: instagram.InstagramEmbed | None = None
    downloaded: media.DownloadedMedia | None = None
    warnings: list[str] = []
    want_frames = settings.ocr or settings.can_see_video
    if settings.vision and not settings.can_see_video:
        warnings.append("Vision skipped: no summary backend is configured, so nothing would see the frames")
    if settings.vision_agentic and settings.can_see_video and not settings.vision_is_agentic:
        warnings.append(
            f"Agentic vision needs an agent backend that can open the frames "
            f"({', '.join(AGENTIC_VISION_PROVIDERS)}); {settings.summary_provider} ran one-shot instead"
        )

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
            clean_url, tmpdir, download=settings.can_transcribe or want_frames, video=want_frames
        )
        warnings.extend(downloaded.warnings)
    except media.MediaFetchError as exc:
        # A photo/carousel post has no video for yt-dlp to find; that is not worth a warning.
        if not (embed and embed.image_urls and "No video formats found" in str(exc)):
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

    duration = (embed.duration if embed else None) or (downloaded.duration if downloaded else None) or 0

    # Sampled once and shared: the summary call sees the frames, OCR reads the same ones.
    frames: list[ocr.Frame] = []
    slides = embed.image_urls if embed and not (downloaded and downloaded.video_path) else ()
    if settings.can_see_video and slides:
        # A carousel / photo post: yt-dlp has no video to give, so the slides themselves are the frames.
        images, slide_warnings = await fetch_slides(slides)
        warnings.extend(slide_warnings)
        try:
            frames = await ocr.frames_from_images(images)
        except Exception as exc:  # noqa: BLE001 (surface as a warning; a text-only summary still runs)
            warnings.append(f"Vision failed: slides could not be decoded: {exc}")
        if not frames:
            warnings.append("Vision skipped: none of the carousel slides could be fetched")
    elif settings.can_see_video and downloaded and downloaded.video_path:
        logger.info("vision (%s): %s", settings.summary_provider, vision_estimate(duration, settings).describe())
        try:
            frames = await ocr.sample_frames(downloaded.video_path, settings)
        except Exception as exc:  # noqa: BLE001 (surface as a warning; a text-only summary still runs)
            warnings.append(f"Vision failed: frames could not be sampled: {exc}")
    elif settings.can_see_video:
        warnings.append("Vision skipped: no video could be downloaded")

    screen_text = None
    if settings.ocr and downloaded and downloaded.video_path:
        logger.info("OCR: %s", ocr.estimate(duration, settings).describe())
        try:
            screen_text, frames_read = await ocr.read_screen_text(downloaded.video_path, settings, frames or None)
            if not screen_text:
                warnings.append(f"OCR read {frames_read} frames and found no on-screen text")
        except Exception as exc:  # noqa: BLE001 (surface as a warning, keep the rest of the note)
            warnings.append(f"OCR failed: {exc}")
    elif settings.ocr:
        warnings.append("OCR skipped: no video could be downloaded")

    sources = tuple(
        s
        for s, present in (
            ("caption", caption),
            ("transcript", transcript),
            ("screen_text", screen_text),
            ("slides" if slides else "video", frames),
        )
        if present
    )
    if not sources:
        raise ReelImportError("; ".join(warnings) or "no caption or audio available")

    posted = (
        datetime.fromtimestamp(downloaded.timestamp, tz=timezone.utc) if downloaded and downloaded.timestamp else None
    )
    content = ReelContent(
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
    return content, frames


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
        content, frames = await gather_content(clean, tmpdir, settings)
    result = await summarize(
        content.caption,
        content.transcript,
        settings,
        title_hint=content.title,
        screen_text=content.screen_text,
        frames=frames,
    )

    settings.output_dir.mkdir(parents=True, exist_ok=True)
    path = _unique_path(
        settings.output_dir, note_filename(content.posted or now, content.creator_handle, result.title), now
    )
    note = build_note(content, result.title, result.summary, result.takeaways, now, result.scenes)
    path.write_text(note, encoding="utf-8")
    logger.info("reel imported: %s sources=%s scenes=%d", path, content.sources, len(result.scenes))
    return ReelImportResult(
        path, result.title, result.summary, result.takeaways, content.sources, content.warnings, result.scenes
    )
