"""Speech-to-text. Two backends:

* ``openai`` — ``gpt-4o-mini-transcribe`` (~$0.003 per minute of audio). Needs OPENAI_API_KEY.
* ``local``  — faster-whisper on your own CPU. No key, no network after the one-time
              model download (~75 MB for ``base``). Install with ``pip install "reelnotes[local]"``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from reelnotes.config import Settings
from reelnotes.media import MAX_AUDIO_BYTES

logger = logging.getLogger(__name__)


async def _transcribe_openai(audio_path: str, settings: Settings) -> str | None:
    from openai import AsyncOpenAI  # lazy: optional dependency

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    audio_bytes = await asyncio.to_thread(Path(audio_path).read_bytes)
    response = await asyncio.wait_for(
        client.audio.transcriptions.create(
            model=settings.openai_transcribe_model,
            file=(Path(audio_path).name, audio_bytes),
        ),
        timeout=120,
    )
    return (getattr(response, "text", "") or "").strip() or None


def _whisper_sync(audio_path: str, model_name: str) -> str | None:
    from faster_whisper import WhisperModel  # lazy: optional, heavy

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    # No VAD filter: it strips sung and music-backed speech, which is most of short-form video.
    segments, _info = model.transcribe(audio_path)
    text = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
    return text or None


async def _transcribe_local(audio_path: str, settings: Settings) -> str | None:
    return await asyncio.to_thread(_whisper_sync, audio_path, settings.whisper_model)


async def transcribe(audio_path: str, settings: Settings) -> str | None:
    """Return the verbatim transcript, or None if the file is too large or the backend returns nothing."""
    size = (await asyncio.to_thread(os.stat, audio_path)).st_size
    if settings.transcribe_provider == "openai" and size > MAX_AUDIO_BYTES:
        logger.warning("audio %s is %d bytes, over the OpenAI upload cap", audio_path, size)
        return None
    if settings.transcribe_provider == "local":
        return await _transcribe_local(audio_path, settings)
    if settings.transcribe_provider == "openai":
        return await _transcribe_openai(audio_path, settings)
    return None
