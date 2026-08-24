"""URL detection, Instagram embed parsing, note rendering, and pipeline orchestration."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from shortform_notes import instagram, media, pipeline, urls
from shortform_notes.config import Settings
from shortform_notes.media import DownloadedMedia, MediaFetchError
from shortform_notes.note import ReelContent, build_note, note_filename, slugify
from shortform_notes.pipeline import ReelImportError, import_reel

NOW = datetime(2026, 8, 23, 20, 30, tzinfo=timezone.utc)


def settings(tmp_path: Path, openai: bool = False, anthropic: bool = False, provider: str | None = None) -> Settings:
    return Settings(
        output_dir=tmp_path / "reels",
        openai_api_key="sk-test" if openai else None,
        anthropic_api_key="sk-ant-test" if anthropic else None,
        summary_provider=provider or ("openai" if openai else "anthropic" if anthropic else "none"),
        transcribe_provider="openai" if openai else "none",
        openai_transcribe_model="gpt-transcribe",
        openai_summary_model="gpt-5-mini",
        anthropic_summary_model="claude-sonnet-5",
        claude_code_model=None,
        codex_model=None,
        whisper_model="base",
        audience="the reader",
        ocr=False,
        ocr_provider="local",
        ocr_fps=1.0,
        ocr_openai_model="gpt-5-mini",
        ocr_anthropic_model="claude-sonnet-5",
        vision=False,
        vision_agentic=False,
        fps_explicit=False,
    )


def embed_html(caption: str = "Symmetry featuring @karanaujla out now", **overrides) -> str:
    """Build the captioned-embed HTML shape Instagram serves (JSON-in-a-JSON-string)."""
    media_ = {
        "shortcode": "DQCkNLtgqEe",
        "is_video": True,
        "display_url": "https://cdn.example/display.jpg",
        "thumbnail_src": "https://cdn.example/thumb.jpg",
        "video_duration": 26.192,
        "video_url": "https://cdn.example/video.mp4",
        "edge_media_to_caption": {"edges": [{"node": {"text": caption}}] if caption else []},
        "owner": {"username": "teddysphotos"},
        **overrides,
    }
    context = json.dumps({"context": {"type": "GraphVideo"}, "gql_data": {"shortcode_media": media_}})
    return '<html><script>{"contextJSON":' + json.dumps(context) + "}</script></html>"


def downloaded(**overrides) -> DownloadedMedia:
    base = dict(
        audio_path=None,
        video_path=None,
        caption="yt-dlp description",
        creator_handle="chef",
        creator_name="Chef Name",
        title=None,
        timestamp=1755000000,
        duration=30.0,
        thumbnail="https://cdn.example/yt.jpg",
        webpage_url="https://www.tiktok.com/@chef/video/1",
    )
    return DownloadedMedia(**{**base, **overrides})


# ── URL detection ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "check this https://www.instagram.com/reel/DQCkNLtgqEe/?igsh=abc",
            "https://www.instagram.com/reel/DQCkNLtgqEe/?igsh=abc",
        ),
        ("https://instagram.com/p/DQCkNLtgqEe/", "https://instagram.com/p/DQCkNLtgqEe/"),
        ("https://www.instagram.com/share/reel/_69O6RoGd", "https://www.instagram.com/share/reel/_69O6RoGd"),
        (
            "https://www.tiktok.com/@chef/video/7301234567890123456",
            "https://www.tiktok.com/@chef/video/7301234567890123456",
        ),
        ("https://vm.tiktok.com/ZMabc123/", "https://vm.tiktok.com/ZMabc123/"),
        ("https://youtube.com/shorts/dQw4w9WgXcQ?si=xyz", "https://youtube.com/shorts/dQw4w9WgXcQ?si=xyz"),
        ("look: https://youtu.be/dQw4w9WgXcQ.", "https://youtu.be/dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", None),
        ("https://example.com/recipe", None),
        ("no link here", None),
    ],
)
def test_detect_reel_url(text, expected):
    assert urls.detect_reel_url(text) == expected


def test_platform_and_tracking_strip():
    assert urls.platform_for("https://www.instagram.com/reel/X/") == "instagram"
    assert urls.platform_for("https://vm.tiktok.com/Z/") == "tiktok"
    assert urls.platform_for("https://youtu.be/Z") == "youtube"
    assert urls.strip_tracking("https://www.instagram.com/reel/X/?igsh=abc#frag") == "https://www.instagram.com/reel/X/"


# ── Instagram embed parsing ────────────────────────────────────────────


def test_parse_embed_context_json():
    embed = instagram.parse_embed(embed_html(), "DQCkNLtgqEe")
    assert embed.caption == "Symmetry featuring @karanaujla out now"
    assert embed.username == "teddysphotos"
    assert embed.video_url == "https://cdn.example/video.mp4"
    assert embed.duration == pytest.approx(26.192)
    assert embed.is_video is True


def test_parse_embed_no_caption():
    assert instagram.parse_embed(embed_html(caption=""), "X").caption is None


def test_parse_embed_caption_div_fallback():
    html = '<div class="Caption"><a>user</a> Line one<br>Line &amp; two</div></div>'
    embed = instagram.parse_embed(html, "ABC")
    assert embed.caption == "user Line one\nLine & two"
    assert embed.shortcode == "ABC"


def test_parse_embed_shell_only_is_none():
    assert instagram.parse_embed("<html><script>window.__d = 1</script></html>" * 100, "X") is None


async def test_resolve_shortcode_direct():
    assert await instagram.resolve_shortcode("https://www.instagram.com/reel/DQCkNLtgqEe/?igsh=1") == "DQCkNLtgqEe"
    assert await instagram.resolve_shortcode("https://www.tiktok.com/@x/video/1") is None


# ── Note rendering ─────────────────────────────────────────────────────


def test_slugify_and_filename():
    assert slugify("Symmetry ft. Karan Aujla — Out NOW!") == "symmetry-ft-karan-aujla-out-now"
    assert slugify("") == "reel"
    assert note_filename(NOW, "teddysphotos", "Symmetry out now") == "2026-08-23-teddysphotos-symmetry-out-now.md"
    assert note_filename(NOW, None, "T") == "2026-08-23-t.md"


def test_build_note_sections():
    content = ReelContent(
        url="https://www.instagram.com/reel/X/",
        platform="instagram",
        caption="Cap line 1\n\nCap line 2",
        transcript="spoken words",
        screen_text="[00:02] 2 cups flour",
        title=None,
        creator_handle="chef",
        creator_name="Chef",
        posted=NOW,
        duration=26.2,
        thumbnail="https://cdn.example/t.jpg",
        sources=("caption", "transcript"),
        warnings=("w1",),
    )
    note = build_note(content, "Title", "A summary.", ("one", "two"), NOW)
    assert note.startswith("---\ntype: reel\nplatform: instagram\n")
    assert 'creator: "@chef"' in note
    assert "sources: [caption, transcript]" in note
    assert "duration_seconds: 26" in note
    assert "## Summary\n\nA summary." in note
    assert "- one\n- two" in note
    assert "> Cap line 1\n>\n> Cap line 2" in note
    assert "## Transcript\n\nspoken words" in note
    assert "## On-screen text\n\n[00:02] 2 cups flour" in note
    assert "- w1" in note


# ── Pipeline ───────────────────────────────────────────────────────────


async def test_import_reel_no_keys_caption_only(tmp_path):
    """Zero-config path: caption + metadata note, no transcript, no summary, no network to LLMs."""
    with (
        patch.object(
            instagram, "fetch_embed", AsyncMock(return_value=instagram.parse_embed(embed_html(), "DQCkNLtgqEe"))
        ),
        patch.object(media, "download_media", AsyncMock(return_value=downloaded(audio_path=None))),
    ):
        result = await import_reel("https://www.instagram.com/reel/DQCkNLtgqEe/?igsh=abc", settings(tmp_path), NOW)
    assert result.sources == ("caption",)
    assert result.path.name == "2025-08-12-teddysphotos-symmetry-featuring-karanaujla-out-now.md"
    text = result.path.read_text()
    assert "Symmetry featuring @karanaujla out now" in text
    assert "_No transcript._" in text
    assert any("Transcription skipped" in w for w in result.warnings)


async def test_import_reel_full_path(tmp_path):
    with (
        patch.object(
            instagram, "fetch_embed", AsyncMock(return_value=instagram.parse_embed(embed_html(), "DQCkNLtgqEe"))
        ),
        patch.object(media, "download_media", AsyncMock(return_value=downloaded(audio_path=str(tmp_path / "a.m4a")))),
        patch.object(pipeline, "transcribe", AsyncMock(return_value="spoken words")),
        patch(
            "shortform_notes.summarize._summarize_openai",
            AsyncMock(return_value={"title": "Symmetry", "summary": "S.", "takeaways": ["t1"]}),
        ),
    ):
        result = await import_reel("https://www.instagram.com/reel/DQCkNLtgqEe/", settings(tmp_path, openai=True), NOW)
    assert result.sources == ("caption", "transcript")
    assert result.title == "Symmetry"
    assert result.takeaways == ("t1",)
    assert result.path.name == "2025-08-12-teddysphotos-symmetry.md"
    assert "spoken words" in result.path.read_text()


async def test_import_reel_anthropic_summary(tmp_path):
    with (
        patch.object(media, "download_media", AsyncMock(return_value=downloaded(audio_path=None))),
        patch(
            "shortform_notes.summarize._summarize_anthropic",
            AsyncMock(return_value={"title": "T", "summary": "S", "takeaways": []}),
        ) as claude,
    ):
        result = await import_reel("https://www.tiktok.com/@chef/video/1", settings(tmp_path, anthropic=True), NOW)
    assert claude.await_count == 1
    assert result.title == "T"
    assert result.sources == ("caption",)


async def test_import_reel_summary_failure_degrades(tmp_path):
    with (
        patch.object(
            media, "download_media", AsyncMock(return_value=downloaded(caption="First line\nmore", audio_path=None))
        ),
        patch("shortform_notes.summarize._summarize_openai", AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        result = await import_reel("https://youtube.com/shorts/dQw4w9WgXcQ", settings(tmp_path, openai=True), NOW)
    assert result.title == "First line"
    assert result.summary == ""
    assert result.path.exists()


async def test_import_reel_nothing_fetched_raises(tmp_path):
    with (
        patch.object(instagram, "fetch_embed", AsyncMock(return_value=None)),
        patch.object(media, "download_media", AsyncMock(side_effect=MediaFetchError("login required"))),
        pytest.raises(ReelImportError, match="login required"),
    ):
        await import_reel("https://www.instagram.com/reel/DQCkNLtgqEe/", settings(tmp_path), NOW)


async def test_import_reel_rejects_unsupported_url(tmp_path):
    with pytest.raises(ReelImportError, match="not a supported"):
        await import_reel("https://example.com/video", settings(tmp_path), NOW)


async def test_duplicate_filename_gets_suffix(tmp_path):
    s = settings(tmp_path)
    with patch.object(media, "download_media", AsyncMock(return_value=downloaded(audio_path=None))):
        first = await import_reel("https://www.tiktok.com/@chef/video/1", s, NOW)
        second = await import_reel("https://www.tiktok.com/@chef/video/1", s, NOW)
    assert first.path != second.path
    assert second.path.name.endswith("-203000.md")
