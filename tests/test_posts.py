"""TikTok photo slideshows and X/Twitter posts: URL handling, metadata fetch, and the pipeline path.

No network: the syndication JSON is a recorded fixture, gallery-dl's extractor is faked,
and yt-dlp (``media.download_media``) is patched as in test_pipeline.
"""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from gallery_dl.extractor.message import Message
from test_pipeline import NOW, downloaded, settings

from shortform_notes import media, ocr, pipeline, posts, tiktok, twitter, urls
from shortform_notes.media import MediaFetchError
from shortform_notes.pipeline import import_reel
from shortform_notes.summarize import Summary

FIXTURES = Path(__file__).parent / "fixtures"
TEXT_POST = json.loads((FIXTURES / "x_syndication_text_post.json").read_text())
PHOTO_URL = "https://www.tiktok.com/@unhinged.behavior/photo/7703585234784144654"
STATUS_URL = "https://x.com/loop_rm_3117211/status/2117301900701786126"


def status(**overrides) -> dict:
    """The recorded text post with extra fields, shaped like the syndication endpoint's mediaDetails."""
    return {**TEXT_POST, **overrides}


PHOTO_MEDIA = [
    {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/A.jpg"},
    {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/B.jpg"},
]
VIDEO_MEDIA = [{"type": "video", "media_url_https": "https://pbs.twimg.com/thumb.jpg", "video_info": {"variants": []}}]


# ── URL recognition and normalisation ──────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        (STATUS_URL, STATUS_URL),
        ("see https://twitter.com/jack/status/20?s=20&t=abc", "https://twitter.com/jack/status/20?s=20&t=abc"),
        ("https://mobile.twitter.com/jack/status/20", "https://mobile.twitter.com/jack/status/20"),
        ("https://x.com/i/status/20", "https://x.com/i/status/20"),
        (PHOTO_URL, PHOTO_URL),
        ("https://www.tiktok.com/t/ZPLmCKAm4/", "https://www.tiktok.com/t/ZPLmCKAm4/"),
        ("https://x.com/jack", None),
        ("https://netflix.com/jack/status/20", None),
    ],
)
def test_detect_x_and_tiktok_photo_urls(text, expected):
    assert urls.detect_reel_url(text) == expected


def test_platform_for_x():
    assert urls.platform_for(STATUS_URL) == "x"
    assert urls.platform_for("https://mobile.twitter.com/jack/status/20") == "x"
    assert urls.platform_for("https://netflix.com/watch/1") == "unknown"


@pytest.mark.parametrize(
    "url,expected",
    [
        (STATUS_URL + "?s=46&t=xyz", STATUS_URL),
        ("https://twitter.com/Jack/status/20/photo/1", "https://x.com/Jack/status/20"),
        ("https://mobile.twitter.com/jack/statuses/20", "https://x.com/jack/status/20"),
        ("https://x.com/i/web/status/20", "https://x.com/i/status/20"),
        ("https://example.com/status/20", None),
    ],
)
def test_x_canonical_url(url, expected):
    assert twitter.canonical_url(url) == expected


def test_tiktok_photo_url_parts():
    assert tiktok.photo_post(PHOTO_URL + "?_r=1&_t=ZP") == ("unhinged.behavior", "7703585234784144654")
    assert tiktok.photo_post("https://www.tiktok.com/@chef/video/1") is None
    assert tiktok.video_url("chef", "1") == "https://www.tiktok.com/@chef/video/1"
    assert tiktok.is_short_link("https://www.tiktok.com/t/ZPLmCKAm4/")
    assert tiktok.is_short_link("https://vm.tiktok.com/ZMabc/")
    assert not tiktok.is_short_link(PHOTO_URL)


# ── X syndication parsing ──────────────────────────────────────────────


def test_parse_status_text_post_from_recorded_fixture():
    post = twitter.parse_status(TEXT_POST)
    assert post.text.startswith("▫️THE CERN COLLIDER WENT DARK FOR 11 MINUTES.")
    assert post.username == "looP_rM_3117211"
    assert post.name == "Mr. Pool"
    assert post.image_urls == ()
    assert post.has_video is False
    assert post.truncated is True  # note_tweet: the long-post body is not in the public payload
    assert post.timestamp == 1793639085  # 2026-11-02T17:04:45Z


def test_parse_status_photos_and_trailing_media_link():
    text = "Look at this https://t.co/abc"
    post = twitter.parse_status(
        status(text=text, display_text_range=[0, 12], mediaDetails=PHOTO_MEDIA, note_tweet=None)
    )
    assert post.text == "Look at this"
    assert post.image_urls == ("https://pbs.twimg.com/media/A.jpg", "https://pbs.twimg.com/media/B.jpg")
    assert post.truncated is False


def test_parse_status_video():
    post = twitter.parse_status(status(mediaDetails=VIDEO_MEDIA))
    assert post.has_video is True
    assert post.image_urls == ()


@pytest.mark.parametrize("data", [{}, {"__typename": "TweetTombstone"}, None])
def test_parse_status_missing_is_none(data):
    assert twitter.parse_status(data) is None


def test_syndication_token_matches_reference_formula():
    # Expected values computed with Node: ((id / 1e15) * Math.PI).toString(36).replace(/(0+|\.)/g, '')
    assert twitter.syndication_token("2117301900701786126") == "54rp7bpkurb"
    assert twitter.syndication_token("20") == "6dq1a2xwd93"
    assert twitter.syndication_token("1790000000000000001") == "4c7g8auqyik"


# ── TikTok slide URLs via gallery-dl ───────────────────────────────────


def fake_extractor(messages):
    return lambda url: iter(messages)


def test_photo_urls_keep_only_image_messages():
    messages = [
        (Message.Directory, "", {"id": "1"}),
        (Message.Url, "https://p16.tiktokcdn.com/1.jpeg", {"type": "image"}),
        (Message.Url, "https://p16.tiktokcdn.com/2.jpeg", {"type": "image"}),
        (Message.Url, "https://sf16.tiktokcdn.com/music.mp3", {"type": "audio"}),
    ]
    with patch("gallery_dl.extractor.find", fake_extractor(messages)):
        assert tiktok._photo_urls_sync(PHOTO_URL) == (
            "https://p16.tiktokcdn.com/1.jpeg",
            "https://p16.tiktokcdn.com/2.jpeg",
        )


def test_photo_urls_no_extractor_is_empty():
    with patch("gallery_dl.extractor.find", lambda url: None):
        assert tiktok._photo_urls_sync(PHOTO_URL) == ()


async def test_fetch_photo_urls_swallows_extractor_errors():
    def boom(url):
        raise RuntimeError("challenge page")

    with patch("gallery_dl.extractor.find", boom):
        assert await tiktok.fetch_photo_urls(PHOTO_URL) == ()


# ── posts.prefetch ─────────────────────────────────────────────────────


async def test_prefetch_tiktok_short_link_to_photo_post():
    with (
        patch.object(tiktok, "resolve_short_link", AsyncMock(return_value=PHOTO_URL + "?_r=1")),
        patch.object(tiktok, "fetch_photo_urls", AsyncMock(return_value=("https://cdn/1.jpeg",))) as fetch,
    ):
        post = await posts.prefetch("https://www.tiktok.com/t/ZPLmCKAm4/", "tiktok")
    fetch.assert_awaited_once_with(PHOTO_URL)
    assert post.url == PHOTO_URL
    # yt-dlp rejects /photo/ but serves the same post's caption and soundtrack from /video/.
    assert post.media_url == "https://www.tiktok.com/@unhinged.behavior/video/7703585234784144654"
    assert post.video is False
    assert post.image_urls == ("https://cdn/1.jpeg",)


async def test_prefetch_tiktok_video_is_untouched():
    with patch.object(tiktok, "resolve_short_link", AsyncMock()) as resolve:
        assert await posts.prefetch("https://www.tiktok.com/@chef/video/1", "tiktok") is None
    resolve.assert_not_awaited()


async def test_prefetch_tiktok_short_link_to_video_keeps_original():
    with patch.object(tiktok, "resolve_short_link", AsyncMock(return_value="https://www.tiktok.com/@chef/video/1")):
        assert await posts.prefetch("https://vm.tiktok.com/ZMabc/", "tiktok") is None


async def test_prefetch_x_text_post_skips_ytdlp():
    with patch.object(twitter, "fetch_status", AsyncMock(return_value=TEXT_POST)):
        post = await posts.prefetch(STATUS_URL + "?s=20", "x")
    assert post.url == STATUS_URL
    assert post.media_url is None
    assert post.caption.startswith("▫️THE CERN COLLIDER")
    assert post.username == "looP_rM_3117211"
    assert any("first 280 characters" in w for w in post.warnings)


async def test_prefetch_x_video_post_goes_to_ytdlp():
    with patch.object(twitter, "fetch_status", AsyncMock(return_value=status(mediaDetails=VIDEO_MEDIA))):
        post = await posts.prefetch(STATUS_URL, "x")
    assert post.media_url == STATUS_URL
    assert post.video is True


async def test_prefetch_x_syndication_failure_falls_back_to_ytdlp():
    with patch.object(twitter, "fetch_status", AsyncMock(return_value=None)):
        post = await posts.prefetch(STATUS_URL, "x")
    assert post.media_url == STATUS_URL
    assert post.caption is None
    assert any("X post metadata" in w for w in post.warnings)


# ── Pipeline ───────────────────────────────────────────────────────────


async def test_import_tiktok_slideshow_fetches_audio_and_ocrs_slides(tmp_path):
    fake_frames = [ocr.Frame(seconds=0.0, png=b"p1"), ocr.Frame(seconds=1.0, png=b"p2")]
    audio = downloaded(audio_path=str(tmp_path / "a.mp3"), caption="slideshow caption", creator_handle="unhinged")
    summarize_mock = AsyncMock(return_value=Summary("Slides", "S", (), ()))
    with (
        patch.object(tiktok, "resolve_short_link", AsyncMock(return_value=PHOTO_URL)),
        patch.object(tiktok, "fetch_photo_urls", AsyncMock(return_value=("https://cdn/1.jpeg", "https://cdn/2.jpeg"))),
        patch.object(media, "download_media", AsyncMock(return_value=audio)) as ytdlp,
        patch.object(pipeline, "transcribe", AsyncMock(return_value="la la la")),
        patch.object(pipeline, "fetch_slides", AsyncMock(return_value=([b"j1", b"j2"], []))) as slides,
        patch.object(ocr, "frames_from_images", AsyncMock(return_value=fake_frames)),
        patch.object(ocr, "read_screen_text", AsyncMock(return_value=("[00:00] WHOSE VOICE?", 2))),
        patch.object(pipeline, "summarize", summarize_mock),
    ):
        result = await import_reel(
            "https://www.tiktok.com/t/ZPLmCKAm4/", replace(settings(tmp_path, openai=True), vision=True), NOW
        )
    assert ytdlp.await_args.args[0] == "https://www.tiktok.com/@unhinged.behavior/video/7703585234784144654"
    assert ytdlp.await_args.kwargs["video"] is False  # the slideshow's only stream is its soundtrack
    slides.assert_awaited_once_with(("https://cdn/1.jpeg", "https://cdn/2.jpeg"))
    assert result.sources == ("caption", "transcript", "screen_text", "slides")
    assert summarize_mock.await_args.kwargs["frames"] == fake_frames
    text = result.path.read_text()
    assert "platform: tiktok" in text
    assert f"source: {PHOTO_URL}" in text


async def test_import_x_text_post_alone(tmp_path):
    with (
        patch.object(twitter, "fetch_status", AsyncMock(return_value=TEXT_POST)),
        patch.object(media, "download_media", AsyncMock()) as ytdlp,
        patch.object(pipeline, "summarize", AsyncMock(return_value=Summary("CERN", "S", (), ()))),
    ):
        result = await import_reel(STATUS_URL + "?s=20", replace(settings(tmp_path, openai=True), vision=True), NOW)
    ytdlp.assert_not_awaited()
    assert result.sources == ("caption",)
    assert not any("Vision skipped" in w or "yt-dlp" in w for w in result.warnings)
    text = result.path.read_text()
    assert "platform: x" in text
    assert f"source: {STATUS_URL}" in text
    assert 'creator: "@looP_rM_3117211"' in text
    assert "posted: 2026-11-02" in text
    assert "THE CERN COLLIDER WENT DARK" in text


async def test_import_x_photo_post_uses_slides(tmp_path):
    fake_frames = [ocr.Frame(seconds=0.0, png=b"p1"), ocr.Frame(seconds=1.0, png=b"p2")]
    with (
        patch.object(twitter, "fetch_status", AsyncMock(return_value=status(mediaDetails=PHOTO_MEDIA))),
        patch.object(media, "download_media", AsyncMock()) as ytdlp,
        patch.object(pipeline, "fetch_slides", AsyncMock(return_value=([b"j1", b"j2"], []))) as slides,
        patch.object(ocr, "frames_from_images", AsyncMock(return_value=fake_frames)),
        patch.object(ocr, "read_screen_text", AsyncMock(return_value=("[00:00] CHART", 2))),
        patch.object(pipeline, "summarize", AsyncMock(return_value=Summary("Photos", "S", (), ()))),
    ):
        result = await import_reel(STATUS_URL, replace(settings(tmp_path, openai=True), vision=True), NOW)
    ytdlp.assert_not_awaited()
    slides.assert_awaited_once_with(("https://pbs.twimg.com/media/A.jpg", "https://pbs.twimg.com/media/B.jpg"))
    assert result.sources == ("caption", "screen_text", "slides")


async def test_import_x_video_post_caption_from_syndication(tmp_path):
    video = downloaded(audio_path=str(tmp_path / "v.mp4"), video_path=str(tmp_path / "v.mp4"), caption="flat text")
    with (
        patch.object(twitter, "fetch_status", AsyncMock(return_value=status(mediaDetails=VIDEO_MEDIA))),
        patch.object(media, "download_media", AsyncMock(return_value=video)) as ytdlp,
        patch.object(pipeline, "transcribe", AsyncMock(return_value="spoken")),
        patch.object(pipeline, "summarize", AsyncMock(return_value=Summary("Video", "S", (), ()))),
    ):
        result = await import_reel(STATUS_URL, settings(tmp_path, openai=True), NOW)
    assert ytdlp.await_args.args[0] == STATUS_URL
    assert result.sources == ("caption", "transcript")
    assert "THE CERN COLLIDER" in result.path.read_text()  # syndication text keeps line breaks; yt-dlp's does not


async def test_import_x_post_nothing_available_raises(tmp_path):
    with (
        patch.object(twitter, "fetch_status", AsyncMock(return_value=None)),
        patch.object(media, "download_media", AsyncMock(side_effect=MediaFetchError("No video could be found"))),
        pytest.raises(pipeline.ReelImportError),
    ):
        await import_reel(STATUS_URL, settings(tmp_path), NOW)
