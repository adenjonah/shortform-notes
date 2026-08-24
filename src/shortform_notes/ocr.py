"""Optional on-screen text (OCR) from sampled video frames.

Off by default. When on, the video is downloaded (not just the audio), frames
are sampled at ``fps`` per second (1 by default; 0 means every frame), near
duplicate frames are dropped, and the rest are read by one of:

* ``local``      RapidOCR (PaddleOCR models on onnxruntime). Free, offline.
* ``openai``     gpt-4o-mini vision, ``detail=low`` (2,833 tokens per frame).
* ``anthropic``  Claude vision (about width*height/750 tokens per frame).

Costs are estimated from the published per-token prices below before any
paid call is made, so the user sees the number first.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import math
from dataclasses import dataclass

from shortform_notes.config import Settings

logger = logging.getLogger(__name__)

# Per-frame token counts and USD per 1M input tokens, from the vendors' pricing pages (checked 2026-08-24).
OPENAI_TOKENS_PER_FRAME_LOW = 2833  # gpt-4o-mini, detail=low
OPENAI_PRICE_PER_M = {"gpt-4o-mini": 0.15}
ANTHROPIC_PRICE_PER_M = {"claude-opus-5": 5.0, "claude-sonnet-5": 3.0, "claude-haiku-4-5": 1.0}
FRAME_MAX_SIDE = 1024  # frames are downscaled to this before OCR; enough for overlay text
DEDUPE_PIXEL_DELTA = 40  # a thumbnail pixel counts as changed when its gray value moves by at least this much
DEDUPE_CHANGED_FRACTION = 0.004  # a frame is "new" when at least this fraction of thumbnail pixels changed
FRAMES_PER_REQUEST = 8

# Contact sheets: frames are tiled into grids before they go to a vision model, which costs
# one image's tokens per GRID_COLS*GRID_ROWS frames and shows the model their order spatially.
GRID_COLS = 4
GRID_ROWS = 4
FRAMES_PER_GRID = GRID_COLS * GRID_ROWS
GRID_CELL_MAX_SIDE = 320  # per cell, so a 4x4 sheet of portrait frames lands near 720x1280

OCR_PROMPT = (
    "These are frames from a short video, in order. For each frame, transcribe every piece of "
    "on-screen text exactly as written (captions, overlays, labels, lists). Return JSON: "
    '{"frames": [{"index": 0, "text": "..."}, ...]}. Use an empty string when a frame has no text. '
    "Do not describe the image; only transcribe text."
)


@dataclass(frozen=True)
class Frame:
    seconds: float
    png: bytes  # PNG-encoded, downscaled


@dataclass(frozen=True)
class Grid:
    """A contact sheet: several frames tiled into one PNG, each cell labelled with its timestamp."""

    png: bytes
    seconds: tuple[float, ...]  # cell timestamps, row-major

    def describe(self) -> str:
        span = f"{timestamp(self.seconds[0])} to {timestamp(self.seconds[-1])}"
        return f"{len(self.seconds)} frames, {span}"


@dataclass(frozen=True)
class OcrEstimate:
    frames: int
    provider: str
    usd: float  # 0.0 for local

    def describe(self) -> str:
        if self.provider == "local":
            return f"about {self.frames} frames, free (local OCR)"
        return f"about {self.frames} frames, about ${self.usd:.3f} via {self.provider}"


# ── estimates ──────────────────────────────────────────────────────────


def frame_count(duration_seconds: float, fps: float, native_fps: float = 30.0) -> int:
    """Frames that will be sampled before de-duplication. fps=0 means every frame."""
    rate = native_fps if fps <= 0 else fps
    return max(1, math.ceil(duration_seconds * rate))


def per_frame_usd(provider: str, model: str, width: int = 720, height: int = 1280) -> float:
    if provider == "openai":
        return OPENAI_TOKENS_PER_FRAME_LOW * OPENAI_PRICE_PER_M.get(model, 0.15) / 1_000_000
    if provider == "anthropic":
        tokens = min(width, FRAME_MAX_SIDE) * min(height, FRAME_MAX_SIDE) / 750
        return tokens * ANTHROPIC_PRICE_PER_M.get(model, 5.0) / 1_000_000
    return 0.0


def estimate(duration_seconds: float, settings: Settings) -> OcrEstimate:
    frames = frame_count(duration_seconds, settings.ocr_fps)
    model = settings.ocr_openai_model if settings.ocr_provider == "openai" else settings.ocr_anthropic_model
    return OcrEstimate(frames, settings.ocr_provider, round(frames * per_frame_usd(settings.ocr_provider, model), 4))


# ── frame extraction (OpenCV; no ffmpeg binary needed) ─────────────────


def _extract_sync(video_path: str, fps: float) -> list[Frame]:
    import cv2  # lazy: optional dependency (opencv-python-headless)
    import numpy as np

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"could not open video {video_path}")
    native = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = 1 if fps <= 0 else max(1, round(native / fps))
    frames: list[Frame] = []
    last_small: np.ndarray | None = None
    index = 0
    while True:
        ok, bgr = cap.read()
        if not ok:
            break
        if index % step == 0:
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA).astype("float32")
            changed = 1.0 if last_small is None else float((np.abs(small - last_small) >= DEDUPE_PIXEL_DELTA).mean())
            if changed >= DEDUPE_CHANGED_FRACTION:
                last_small = small
                h, w = bgr.shape[:2]
                scale = min(1.0, FRAME_MAX_SIDE / max(h, w))
                if scale < 1.0:
                    bgr = cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
                ok_png, buf = cv2.imencode(".png", bgr)
                if ok_png:
                    frames.append(Frame(seconds=index / native, png=buf.tobytes()))
        index += 1
    cap.release()
    return frames


async def extract_frames(video_path: str, fps: float) -> list[Frame]:
    return await asyncio.to_thread(_extract_sync, video_path, fps)


def timestamp(seconds: float) -> str:
    mm, ss = divmod(int(seconds), 60)
    return f"{mm:02d}:{ss:02d}"


# ── contact sheets ─────────────────────────────────────────────────────


def _label(cell, text: str) -> None:
    """Burn a timestamp into the cell's top-left corner, on a filled box so it reads on any frame."""
    import cv2

    font, scale, thickness = cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1
    (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
    cv2.rectangle(cell, (0, 0), (tw + 8, th + 8), (0, 0, 0), -1)
    cv2.putText(cell, text, (4, th + 4), font, scale, (255, 255, 255), thickness, cv2.LINE_AA)


def _tile_sync(frames: list[Frame], cols: int, rows: int, cell_max_side: int) -> list[Grid]:
    import cv2
    import numpy as np

    per_sheet = cols * rows
    grids: list[Grid] = []
    for start in range(0, len(frames), per_sheet):
        batch = frames[start : start + per_sheet]
        cells = []
        for frame in batch:
            img = cv2.imdecode(np.frombuffer(frame.png, dtype="uint8"), cv2.IMREAD_COLOR)
            if img is None:
                continue
            h, w = img.shape[:2]
            scale = min(1.0, cell_max_side / max(h, w))
            img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
            _label(img, timestamp(frame.seconds))
            cells.append(img)
        if not cells:
            continue
        # One cell size for the whole sheet (every frame shares the video's aspect ratio); pad the last row.
        ch, cw = cells[0].shape[:2]
        cells = [c if c.shape[:2] == (ch, cw) else cv2.resize(c, (cw, ch)) for c in cells]
        blank = np.zeros((ch, cw, 3), dtype="uint8")
        padded = cells + [blank] * (-len(cells) % cols)
        sheet = np.vstack([np.hstack(padded[i : i + cols]) for i in range(0, len(padded), cols)])
        ok, buf = cv2.imencode(".png", sheet)
        if ok:
            grids.append(Grid(png=buf.tobytes(), seconds=tuple(f.seconds for f in batch[: len(cells)])))
    return grids


async def tile_frames(
    frames: list[Frame], cols: int = GRID_COLS, rows: int = GRID_ROWS, cell_max_side: int = GRID_CELL_MAX_SIDE
) -> list[Grid]:
    """Tile frames into chronological contact sheets, ``cols*rows`` frames per sheet."""
    if not frames:
        return []
    return await asyncio.to_thread(_tile_sync, frames, cols, rows, cell_max_side)


# ── OCR backends ───────────────────────────────────────────────────────


def _ocr_local_sync(frames: list[Frame]) -> list[str]:
    import cv2
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR  # lazy: optional dependency

    engine = RapidOCR()
    texts: list[str] = []
    for frame in frames:
        img = cv2.imdecode(np.frombuffer(frame.png, dtype="uint8"), cv2.IMREAD_COLOR)
        result, _ = engine(img)
        texts.append(" ".join(line[1] for line in (result or []) if line[1]).strip())
    return texts


async def _ocr_local(frames: list[Frame], settings: Settings) -> list[str]:
    return await asyncio.to_thread(_ocr_local_sync, frames)


def _parse_batch(text: str, count: int) -> list[str]:
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError:
        return [""] * count
    by_index = {int(f.get("index", i)): str(f.get("text") or "") for i, f in enumerate(data.get("frames") or [])}
    return [by_index.get(i, "") for i in range(count)]


async def _ocr_openai(frames: list[Frame], settings: Settings) -> list[str]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    out: list[str] = []
    for start in range(0, len(frames), FRAMES_PER_REQUEST):
        batch = frames[start : start + FRAMES_PER_REQUEST]
        content = [{"type": "text", "text": OCR_PROMPT}]
        for i, frame in enumerate(batch):
            b64 = base64.b64encode(frame.png).decode()
            content.append({"type": "text", "text": f"frame {i}"})
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "low"}})
        response = await client.chat.completions.create(
            model=settings.ocr_openai_model,
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
            max_tokens=2000,
        )
        out.extend(_parse_batch(response.choices[0].message.content or "", len(batch)))
    return out


async def _ocr_anthropic(frames: list[Frame], settings: Settings) -> list[str]:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    out: list[str] = []
    for start in range(0, len(frames), FRAMES_PER_REQUEST):
        batch = frames[start : start + FRAMES_PER_REQUEST]
        content: list[dict] = []
        for i, frame in enumerate(batch):
            content.append({"type": "text", "text": f"frame {i}"})
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(frame.png).decode(),
                    },
                }
            )
        content.append({"type": "text", "text": OCR_PROMPT})
        response = await client.messages.create(
            model=settings.ocr_anthropic_model,
            max_tokens=4000,
            messages=[{"role": "user", "content": content}],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "frames": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {"index": {"type": "integer"}, "text": {"type": "string"}},
                                    "required": ["index", "text"],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": ["frames"],
                        "additionalProperties": False,
                    },
                }
            },
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("model declined to read these frames")
        text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
        out.extend(_parse_batch(text, len(batch)))
    return out


_BACKENDS = {"local": "_ocr_local", "openai": "_ocr_openai", "anthropic": "_ocr_anthropic"}


def merge_text(frames: list[Frame], texts: list[str]) -> str | None:
    """Timestamped lines, consecutive duplicates collapsed. None when nothing was read."""
    lines: list[str] = []
    previous = ""
    for frame, text in zip(frames, texts, strict=False):
        clean = " ".join(text.split())
        if clean and clean != previous:
            lines.append(f"[{timestamp(frame.seconds)}] {clean}")
        if clean:
            previous = clean
    return "\n".join(lines) or None


async def read_screen_text(
    video_path: str, settings: Settings, frames: list[Frame] | None = None
) -> tuple[str | None, int]:
    """Sample, de-duplicate and OCR frames. Returns (text, frames_read).

    ``frames`` lets a caller that already sampled the video (the vision path)
    reuse them instead of decoding it a second time.
    """
    frames = await extract_frames(video_path, settings.ocr_fps) if frames is None else frames
    if not frames:
        return None, 0
    backend = globals()[_BACKENDS[settings.ocr_provider]]
    texts = await backend(frames, settings)
    return merge_text(frames, texts), len(frames)


def to_png_bytes(image) -> bytes:  # small helper for tests that build frames with Pillow
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
