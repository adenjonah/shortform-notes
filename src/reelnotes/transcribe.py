"""Speech-to-text via OpenAI's transcription endpoint (~$0.003 per minute of audio)."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from reelnotes.config import Settings
from reelnotes.media import MAX_AUDIO_BYTES

logger = logging.getLogger(__name__)


async def transcribe(audio_path: str, settings: Settings) -> str | None:
    """Return the verbatim transcript, or None if the file is too large or the call fails."""
    size = (await asyncio.to_thread(os.stat, audio_path)).st_size
    if size > MAX_AUDIO_BYTES:
        logger.warning("audio %s is %d bytes, over the transcription cap", audio_path, size)
        return None
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
