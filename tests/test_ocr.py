"""OCR: cost estimates, frame sampling and de-duplication, local backend, merge, pipeline wiring."""

from unittest.mock import AsyncMock, patch

import pytest

from shortform_notes import config, media, ocr, pipeline
from shortform_notes.media import DownloadedMedia
from shortform_notes.pipeline import import_reel

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")


def settings(tmp_path, **over) -> config.Settings:
    base = dict(
        output_dir=tmp_path / "reels",
        openai_api_key=None,
        anthropic_api_key=None,
        summary_provider="none",
        transcribe_provider="none",
        openai_transcribe_model="x",
        openai_summary_model="x",
        anthropic_summary_model="x",
        claude_code_model=None,
        codex_model=None,
        whisper_model="base",
        audience="the reader",
        ocr=True,
        ocr_provider="local",
        ocr_fps=1.0,
        ocr_openai_model="gpt-4o-mini",
        ocr_anthropic_model="claude-opus-5",
    )
    return config.Settings(**{**base, **over})


def make_video(path, seconds=4, fps=10, texts=("2 cups flour", "2 cups flour", "bake 425F", "bake 425F")):
    """Tiny mp4: one line of large text per second, so 1 fps sampling + dedupe yields 2 distinct frames."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (640, 360))
    for sec in range(seconds):
        frame = np.full((360, 640, 3), 255, dtype=np.uint8)
        cv2.putText(frame, texts[sec], (30, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 4)
        for _ in range(fps):
            writer.write(frame)
    writer.release()
    return str(path)


# estimates


def test_frame_count_and_cost(tmp_path):
    assert ocr.frame_count(30, 1.0) == 30
    assert ocr.frame_count(30, 0) == 900  # every frame at an assumed 30 fps
    assert ocr.frame_count(0.2, 1.0) == 1
    s_local = settings(tmp_path)
    assert ocr.estimate(30, s_local).usd == 0 and "free" in ocr.estimate(30, s_local).describe()
    s_openai = settings(tmp_path, ocr_provider="openai")
    e = ocr.estimate(30, s_openai)
    assert e.frames == 30 and e.usd == pytest.approx(30 * 2833 * 0.15 / 1e6, rel=1e-2)
    s_claude = settings(tmp_path, ocr_provider="anthropic")
    assert ocr.estimate(30, s_claude).usd > e.usd  # opus-5 vision is the expensive option


# frames


async def test_extract_frames_samples_and_dedupes(tmp_path):
    video = make_video(tmp_path / "v.mp4")
    frames = await ocr.extract_frames(video, 1.0)
    assert 2 <= len(frames) <= 3  # 4 sampled, identical neighbours dropped
    assert frames[0].seconds == 0
    every = await ocr.extract_frames(video, 0)
    assert len(every) == len(frames)  # dedupe collapses identical frames even when reading all of them


async def test_local_ocr_reads_overlay_text(tmp_path):
    pytest.importorskip("rapidocr_onnxruntime")
    video = make_video(tmp_path / "v.mp4")
    text, n = await ocr.read_screen_text(video, settings(tmp_path))
    assert n >= 2
    assert "flour" in text.lower() and "425" in text
    assert text.startswith("[00:00]")


def test_merge_collapses_repeats():
    frames = [ocr.Frame(0, b""), ocr.Frame(1, b""), ocr.Frame(2, b""), ocr.Frame(65, b"")]
    assert ocr.merge_text(frames, ["a b", "a  b", "", "c"]) == "[00:00] a b\n[01:05] c"
    assert ocr.merge_text(frames, ["", "", "", ""]) is None


# config + cli


def test_config_ocr_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "none.env")
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SHORTFORM_NOTES_OCR", "SHORTFORM_NOTES_OCR_FPS"):
        monkeypatch.delenv(k, raising=False)
    s = config.load_settings(transcribe_provider="none", summary_provider="none")
    assert s.ocr is False and s.ocr_fps == 1.0 and s.ocr_provider == "local"
    monkeypatch.setenv("SHORTFORM_NOTES_OCR", "1")
    monkeypatch.setenv("SHORTFORM_NOTES_OCR_FPS", "0")
    s = config.load_settings(transcribe_provider="none", summary_provider="none")
    assert s.ocr is True and s.ocr_fps == 0.0
    assert config.load_settings(transcribe_provider="none", summary_provider="none", ocr=False).ocr is False


# pipeline


async def test_pipeline_adds_screen_text_source(tmp_path):
    dl = DownloadedMedia(
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
    with (
        patch.object(media, "download_media", AsyncMock(return_value=dl)) as dm,
        patch.object(ocr, "read_screen_text", AsyncMock(return_value=("[00:01] 2 cups flour", 12))),
    ):
        result = await import_reel("https://www.tiktok.com/@c/video/1", settings(tmp_path))
    assert dm.await_args.kwargs["video"] is True
    assert result.sources == ("caption", "screen_text")
    assert "## On-screen text" in result.path.read_text()


async def test_pipeline_ocr_failure_is_a_warning(tmp_path):
    dl = DownloadedMedia(
        audio_path=None,
        video_path="/tmp/fake.mp4",
        caption="cap",
        creator_handle=None,
        creator_name=None,
        title=None,
        timestamp=None,
        duration=5.0,
        thumbnail=None,
        webpage_url="u",
    )
    with (
        patch.object(media, "download_media", AsyncMock(return_value=dl)),
        patch.object(ocr, "read_screen_text", AsyncMock(side_effect=RuntimeError("no model"))),
    ):
        result = await import_reel("https://youtube.com/shorts/dQw4w9WgXcQ", settings(tmp_path))
    assert result.sources == ("caption",)
    assert any("OCR failed" in w for w in result.warnings)
    assert pipeline is not None
