"""Vision: frames on the summary call, the frame cap, and the unsupported-backend fallback.

No network and no SDK required: ``openai`` and ``anthropic`` are replaced by stub
modules whose clients record the payload they were handed.
"""

import json
import sys
import types
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from shortform_notes import config, media, ocr, summarize
from shortform_notes.media import DownloadedMedia
from shortform_notes.pipeline import import_reel
from shortform_notes.summarize import MAX_VISION_FRAMES, select_frames, vision_estimate

REPLY = {"title": "T", "summary": "S", "takeaways": ["a"]}


def settings(tmp_path, **over) -> config.Settings:
    base = dict(
        output_dir=tmp_path / "reels",
        openai_api_key="sk-test",
        anthropic_api_key="sk-ant-test",
        summary_provider="openai",
        transcribe_provider="none",
        openai_transcribe_model="x",
        openai_summary_model="gpt-4o-mini",
        anthropic_summary_model="claude-opus-5",
        claude_code_model=None,
        codex_model=None,
        whisper_model="base",
        audience="the reader",
        ocr=False,
        ocr_provider="local",
        ocr_fps=1.0,
        ocr_openai_model="gpt-4o-mini",
        ocr_anthropic_model="claude-opus-5",
        vision=True,
    )
    return config.Settings(**{**base, **over})


def frames(count: int, step: float = 1.0) -> list[ocr.Frame]:
    return [ocr.Frame(seconds=i * step, png=f"png{i}".encode()) for i in range(count)]


def video(**over) -> DownloadedMedia:
    base = dict(
        audio_path=None,
        video_path="/tmp/fake.mp4",
        caption="cap",
        creator_handle="c",
        creator_name="C",
        title=None,
        timestamp=None,
        duration=12.0,
        thumbnail=None,
        webpage_url="u",
    )
    return DownloadedMedia(**{**base, **over})


@contextmanager
def stub_openai(recorder: dict):
    """Replace the ``openai`` module; the client records the create() kwargs."""

    class Completions:
        async def create(self, **kwargs):
            recorder.update(kwargs)
            message = SimpleNamespace(content=json.dumps(REPLY))
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    module = types.ModuleType("openai")
    module.AsyncOpenAI = lambda **_: SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    with patch.dict(sys.modules, {"openai": module}):
        yield


@contextmanager
def stub_anthropic(recorder: dict):
    class Messages:
        async def create(self, **kwargs):
            recorder.update(kwargs)
            block = SimpleNamespace(type="text", text=json.dumps(REPLY))
            return SimpleNamespace(stop_reason="end_turn", content=[block])

    module = types.ModuleType("anthropic")
    module.AsyncAnthropic = lambda **_: SimpleNamespace(messages=Messages())
    with patch.dict(sys.modules, {"anthropic": module}):
        yield


# ── frames on the summary call ─────────────────────────────────────────


async def test_frames_reach_the_openai_payload(tmp_path):
    sent = {}
    with stub_openai(sent):
        result = await summarize.summarize("cap", "spoken", settings(tmp_path), frames=frames(3))
    assert result.title == "T"
    content = sent["messages"][1]["content"]
    images = [part for part in content if part["type"] == "image_url"]
    assert len(images) == 3
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert images[0]["image_url"]["detail"] == "low"
    assert content[0]["text"].startswith("Caption:\ncap")
    assert "frame at 00:02" in [part.get("text") for part in content]
    assert "frames sampled from the video" in sent["messages"][0]["content"]


async def test_frames_reach_the_anthropic_payload(tmp_path):
    sent = {}
    with stub_anthropic(sent):
        result = await summarize.summarize(
            "cap", None, settings(tmp_path, summary_provider="anthropic"), frames=frames(2)
        )
    assert result.title == "T"
    content = sent["messages"][0]["content"]
    images = [part for part in content if part["type"] == "image"]
    assert len(images) == 2
    assert images[0]["source"]["media_type"] == "image/png"
    assert content[-1]["text"].startswith("Caption:\ncap")  # the text prompt comes after the frames
    assert "frames sampled from the video" in sent["system"]


async def test_no_frames_keeps_the_plain_text_payload(tmp_path):
    sent = {}
    with stub_openai(sent):
        await summarize.summarize("cap", None, settings(tmp_path, vision=False))
    assert sent["messages"][1]["content"].startswith("Caption:\ncap")
    assert "frames sampled from the video" not in sent["messages"][0]["content"]


# ── the cap ────────────────────────────────────────────────────────────


def test_select_frames_caps_and_spreads():
    assert select_frames(frames(5)) == frames(5)
    picked = select_frames(frames(100))
    assert len(picked) == MAX_VISION_FRAMES
    assert picked[0].seconds == 0 and picked[-1].seconds == 99
    assert [f.seconds for f in picked] == sorted({f.seconds for f in picked})
    assert len(select_frames(frames(9), limit=3)) == 3


async def test_summarize_never_sends_more_than_the_cap(tmp_path):
    sent = {}
    with stub_openai(sent):
        await summarize.summarize("cap", None, settings(tmp_path), frames=frames(60))
    images = [part for part in sent["messages"][1]["content"] if part["type"] == "image_url"]
    assert len(images) == MAX_VISION_FRAMES


def test_vision_estimate_is_capped_and_priced(tmp_path):
    count, usd = vision_estimate(60, settings(tmp_path))
    assert count == MAX_VISION_FRAMES
    assert usd == pytest.approx(MAX_VISION_FRAMES * 2833 * 0.15 / 1e6, rel=1e-2)
    assert vision_estimate(5, settings(tmp_path)) == (5, pytest.approx(5 * 2833 * 0.15 / 1e6, abs=1e-4))
    assert vision_estimate(60, settings(tmp_path, summary_provider="anthropic"))[1] > usd


# ── pipeline wiring ────────────────────────────────────────────────────


async def test_pipeline_sends_frames_and_records_the_video_source(tmp_path):
    with (
        patch.object(media, "download_media", AsyncMock(return_value=video())) as download,
        patch.object(ocr, "extract_frames", AsyncMock(return_value=frames(3))),
        patch("shortform_notes.summarize._summarize_openai", AsyncMock(return_value=REPLY)) as backend,
    ):
        result = await import_reel("https://www.tiktok.com/@c/video/1", settings(tmp_path))
    assert download.await_args.kwargs["video"] is True
    assert result.sources == ("caption", "video")
    assert len(backend.await_args.args[4]) == 3
    assert "sources: [caption, video]" in result.path.read_text()


async def test_vision_reuses_the_frames_ocr_would_have_sampled(tmp_path):
    with (
        patch.object(media, "download_media", AsyncMock(return_value=video())),
        patch.object(ocr, "extract_frames", AsyncMock(return_value=frames(3))) as extract,
        patch.object(ocr, "read_screen_text", AsyncMock(return_value=("[00:01] 2 cups flour", 3))) as read,
        patch("shortform_notes.summarize._summarize_openai", AsyncMock(return_value=REPLY)),
    ):
        result = await import_reel("https://www.tiktok.com/@c/video/1", settings(tmp_path, ocr=True))
    assert extract.await_count == 1  # sampled once, not once per consumer
    assert read.await_args.args[2] == frames(3)
    assert result.sources == ("caption", "screen_text", "video")


async def test_unsupported_backend_warns_and_still_summarizes(tmp_path):
    with (
        patch.object(media, "download_media", AsyncMock(return_value=video())) as download,
        patch("shortform_notes.summarize._summarize_claude_code", AsyncMock(return_value=REPLY)) as backend,
    ):
        result = await import_reel(
            "https://www.tiktok.com/@c/video/1", settings(tmp_path, summary_provider="claude-code")
        )
    assert result.title == "T"
    assert result.sources == ("caption",)
    assert any("Vision skipped" in w and "claude-code" in w for w in result.warnings)
    assert download.await_args.kwargs["video"] is False  # no point paying for the whole mp4
    assert backend.await_args.args[4] == ()


async def test_frame_sampling_failure_is_a_warning(tmp_path):
    with (
        patch.object(media, "download_media", AsyncMock(return_value=video())),
        patch.object(ocr, "extract_frames", AsyncMock(side_effect=RuntimeError("no codec"))),
        patch("shortform_notes.summarize._summarize_openai", AsyncMock(return_value=REPLY)),
    ):
        result = await import_reel("https://www.tiktok.com/@c/video/1", settings(tmp_path))
    assert result.sources == ("caption",)
    assert any("Vision failed" in w for w in result.warnings)


async def test_vision_skipped_when_no_video_downloaded(tmp_path):
    with (
        patch.object(media, "download_media", AsyncMock(return_value=video(video_path=None))),
        patch("shortform_notes.summarize._summarize_openai", AsyncMock(return_value=REPLY)),
    ):
        result = await import_reel("https://www.tiktok.com/@c/video/1", settings(tmp_path))
    assert any("Vision skipped: no video" in w for w in result.warnings)


# ── flag, env and config precedence ────────────────────────────────────


def _load(**over) -> config.Settings:
    return config.load_settings(summary_provider="openai", transcribe_provider="none", **over)


def test_vision_defaults_off(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "none.env")
    monkeypatch.delenv("SHORTFORM_NOTES_VISION", raising=False)
    assert _load().vision is False


def test_vision_flag_beats_env_beats_config_file(monkeypatch, tmp_path):
    path = tmp_path / "config.env"
    config.write_config_file({"SHORTFORM_NOTES_VISION": "1"}, path)
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.delenv("SHORTFORM_NOTES_VISION", raising=False)
    assert _load().vision is True
    monkeypatch.setenv("SHORTFORM_NOTES_VISION", "off")
    assert _load().vision is False
    assert _load(vision=True).vision is True
    monkeypatch.setenv("SHORTFORM_NOTES_VISION", "1")
    assert _load(vision=False).vision is False


def test_can_see_video_only_for_image_capable_backends(tmp_path):
    assert settings(tmp_path).can_see_video is True
    assert settings(tmp_path, summary_provider="anthropic").can_see_video is True
    for provider in ("claude-code", "codex", "none"):
        assert settings(tmp_path, summary_provider=provider).can_see_video is False
    assert settings(tmp_path, vision=False).can_see_video is False


def test_cli_flags_reach_settings(tmp_path, monkeypatch):
    from shortform_notes.cli import main
    from shortform_notes.pipeline import ReelImportResult

    monkeypatch.delenv("SHORTFORM_NOTES_VISION", raising=False)
    result = ReelImportResult(tmp_path / "n.md", "T", "S", (), ("caption",), ())
    with patch("shortform_notes.cli.import_reel", AsyncMock(return_value=result)) as run:
        main(["https://youtu.be/x", "--vision", "--summary", "openai", "-o", str(tmp_path)])
        assert run.await_args.args[1].vision is True
        main(["https://youtu.be/x", "--no-vision", "--summary", "openai", "-o", str(tmp_path)])
        assert run.await_args.args[1].vision is False
