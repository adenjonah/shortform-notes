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
        vision=False,
        fps_explicit=False,
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


# cut-aware sampling (the ffmpeg subprocess is faked)


def fake_ffmpeg(shades, times=None):
    """Stands in for ffmpeg: writes one flat-colour PNG per shade and reports their times on stderr."""
    times = list(range(len(shades))) if times is None else times

    async def run(argv):
        pattern = argv[-1]
        for i, shade in enumerate(shades):
            cv2.imwrite(pattern % (i + 1), np.full((320, 180, 3), shade, dtype=np.uint8))
        lines = [f"[Parsed_showinfo_0 @ 0x1] n:{i} pts:{i} pts_time:{t} pos:1" for i, t in enumerate(times)]
        return 0, "\n".join(lines)

    return run


def test_ffmpeg_argv_decodes_keyframes_only():
    argv = ocr.ffmpeg_argv("/tmp/v.mp4", "/tmp/out/kf-%05d.png")
    assert argv[0] == "ffmpeg"
    assert argv[argv.index("-skip_frame") + 1] == "nokey"
    assert argv[argv.index("-i") + 1] == "/tmp/v.mp4"
    assert "showinfo" in argv and argv[-1] == "/tmp/out/kf-%05d.png"


def test_parse_showinfo_reads_frame_times():
    stderr = "n:0 pts:0 pts_time:0 dur:1\nn:1 pts_time:1.234 fmt:yuv420p\nunrelated ffmpeg chatter"
    assert ocr.parse_showinfo(stderr) == [0.0, 1.234]
    assert ocr.parse_showinfo("") == []


async def test_sample_frames_follows_the_cuts_when_ffmpeg_is_there(tmp_path):
    with (
        patch.object(ocr, "has_ffmpeg", lambda: True),
        patch.object(ocr, "_run_ffmpeg", fake_ffmpeg([0, 60, 120, 180, 240], times=[0, 1.2, 3.6, 6.0, 7.2])),
        patch.object(ocr, "extract_frames", AsyncMock()) as clock,
    ):
        frames = await ocr.sample_frames("/tmp/v.mp4", settings(tmp_path))
    assert [f.seconds for f in frames] == [0, 1.2, 3.6, 6.0, 7.2]  # the keyframe times, not 0,1,2,3
    assert frames[0].png.startswith(b"\x89PNG")
    clock.assert_not_awaited()


async def test_keyframes_still_go_through_the_deduper(tmp_path):
    with (
        patch.object(ocr, "has_ffmpeg", lambda: True),
        patch.object(ocr, "_run_ffmpeg", fake_ffmpeg([0, 0, 60, 120, 120, 180, 240])),
    ):
        frames = await ocr.sample_frames("/tmp/v.mp4", settings(tmp_path))
    assert [f.seconds for f in frames] == [0, 2, 3, 5, 6]  # one per distinct shot, at its first appearance


async def test_dense_keyframes_are_thinned_to_an_even_spread(tmp_path):
    shades = [i * 25 for i in range(10)]  # an all-intra video: every frame is a keyframe
    with (
        patch.object(ocr, "MAX_KEYFRAMES", 4),
        patch.object(ocr, "has_ffmpeg", lambda: True),
        patch.object(ocr, "_run_ffmpeg", fake_ffmpeg(shades)),
    ):
        frames = await ocr.sample_frames("/tmp/v.mp4", settings(tmp_path))
    assert [f.seconds for f in frames] == [0, 2, 5, 7]  # spread across the video, not its first four frames


async def test_sample_frames_falls_back_to_the_clock_without_ffmpeg(tmp_path):
    with (
        patch.object(ocr.shutil, "which", lambda _: None),
        patch.object(ocr, "_run_ffmpeg", AsyncMock()) as ffmpeg,
        patch.object(ocr, "extract_frames", AsyncMock(return_value=[])) as clock,
    ):
        await ocr.sample_frames("/tmp/v.mp4", settings(tmp_path, ocr_fps=2.0))
    clock.assert_awaited_once_with("/tmp/v.mp4", 2.0)
    ffmpeg.assert_not_awaited()


async def test_too_few_keyframes_fall_back_to_the_clock(tmp_path):
    sampled = [ocr.Frame(0.0, b"png")]
    with (
        patch.object(ocr, "has_ffmpeg", lambda: True),
        patch.object(ocr, "_run_ffmpeg", fake_ffmpeg([0, 120])),  # under MIN_KEYFRAMES
        patch.object(ocr, "extract_frames", AsyncMock(return_value=sampled)),
    ):
        assert await ocr.sample_frames("/tmp/v.mp4", settings(tmp_path)) == sampled


async def test_ffmpeg_failure_falls_back_to_the_clock(tmp_path):
    async def broken(argv):
        return 1, "v.mp4: Invalid data found when processing input"

    sampled = [ocr.Frame(0.0, b"png")]
    with (
        patch.object(ocr, "has_ffmpeg", lambda: True),
        patch.object(ocr, "_run_ffmpeg", broken),
        patch.object(ocr, "extract_frames", AsyncMock(return_value=sampled)),
    ):
        assert await ocr.sample_frames("/tmp/v.mp4", settings(tmp_path)) == sampled


async def test_an_explicit_fps_forces_clock_sampling(tmp_path):
    with (
        patch.object(ocr, "has_ffmpeg", lambda: True),
        patch.object(ocr, "_run_ffmpeg", AsyncMock()) as ffmpeg,
        patch.object(ocr, "extract_frames", AsyncMock(return_value=[])) as clock,
    ):
        await ocr.sample_frames("/tmp/v.mp4", settings(tmp_path, fps_explicit=True, ocr_fps=0.5))
    clock.assert_awaited_once_with("/tmp/v.mp4", 0.5)
    ffmpeg.assert_not_awaited()


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


def test_fps_explicit_records_whether_a_rate_was_asked_for(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "none.env")
    monkeypatch.delenv("SHORTFORM_NOTES_OCR_FPS", raising=False)
    base = dict(transcribe_provider="none", summary_provider="none")
    assert config.load_settings(**base).fps_explicit is False  # default rate: ffmpeg may follow the cuts instead
    assert config.load_settings(**base, ocr_fps=2.0).fps_explicit is True
    monkeypatch.setenv("SHORTFORM_NOTES_OCR_FPS", "0.5")
    assert config.load_settings(**base).fps_explicit is True


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
