"""One LLM call over caption + transcript → title, summary, takeaways.

Two providers, same JSON contract. Summaries are best-effort: on any failure the
note still ships with the verbatim caption and transcript, just no summary.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from reelnotes.config import Settings

logger = logging.getLogger(__name__)

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Short descriptive title, max 8 words, no hashtags, no emoji"},
        "summary": {"type": "string", "description": "2-4 sentences: what the video is about and its main point"},
        "takeaways": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3-6 concrete, specific takeaways — exact quantities, steps, names, numbers when present",
        },
    },
    "required": ["title", "summary", "takeaways"],
    "additionalProperties": False,
}


def build_prompt(audience: str) -> str:
    return (
        f"You are turning a short social-media video into a personal note for {audience}. "
        "You are given the video's caption and/or spoken transcript. Return JSON with keys "
        "title (max 8 words, no hashtags, no emoji), summary (2-4 sentences: what the video is "
        "about and its main point) and takeaways (3-6 concrete, specific bullets — exact "
        "quantities, steps, names, numbers when present).\n"
        "Rules: never invent details that are not in the caption or transcript. If the video is "
        "a recipe, the takeaways must list every ingredient with amounts and the steps in order. "
        "Plain text only."
    )


@dataclass(frozen=True)
class Summary:
    title: str
    summary: str
    takeaways: tuple[str, ...]


def _user_message(caption: str | None, transcript: str | None) -> str:
    return f"Caption:\n{caption or '(none)'}\n\nTranscript:\n{transcript or '(none)'}"


def _coerce(data: dict, fallback_title: str) -> Summary:
    takeaways = tuple(str(t).strip() for t in data.get("takeaways") or [] if str(t).strip())
    return Summary(
        title=(data.get("title") or fallback_title).strip(),
        summary=(data.get("summary") or "").strip(),
        takeaways=takeaways,
    )


async def _summarize_openai(caption: str | None, transcript: str | None, settings: Settings) -> dict:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.chat.completions.create(
        model=settings.openai_summary_model,
        messages=[
            {"role": "system", "content": build_prompt(settings.audience)},
            {"role": "user", "content": _user_message(caption, transcript)},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "reel_summary", "strict": True, "schema": SUMMARY_SCHEMA},
        },
        temperature=0.2,
        max_tokens=800,
    )
    return json.loads(response.choices[0].message.content or "{}")


async def _summarize_anthropic(caption: str | None, transcript: str | None, settings: Settings) -> dict:
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    response = await client.messages.create(
        model=settings.anthropic_summary_model,
        max_tokens=2000,
        system=build_prompt(settings.audience),
        messages=[{"role": "user", "content": _user_message(caption, transcript)}],
        output_config={"format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}},
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined to summarize this video")
    text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
    return json.loads(text or "{}")


async def summarize(
    caption: str | None, transcript: str | None, settings: Settings, title_hint: str | None = None
) -> Summary:
    """Return a Summary; degrades to a caption-derived title when the LLM step fails or is off."""
    fallback_title = (title_hint or caption or transcript or "Reel").split("\n")[0][:60]
    if not settings.can_summarize:
        return Summary(fallback_title, "", ())
    try:
        if settings.summary_provider == "anthropic":
            data = await _summarize_anthropic(caption, transcript, settings)
        else:
            data = await _summarize_openai(caption, transcript, settings)
    except Exception as exc:  # noqa: BLE001 — summary is best-effort; verbatim note still ships
        logger.warning("summary failed (%s): %s", settings.summary_provider, exc)
        return Summary(fallback_title, "", ())
    return _coerce(data, fallback_title)
