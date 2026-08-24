"""One LLM call over caption and transcript, returning title, summary and takeaways.

Backends (all produce the same JSON shape):

* ``openai``      Chat Completions with a strict JSON schema.
* ``anthropic``   Messages API with ``output_config`` JSON schema.
* ``claude-code`` shells out to ``claude -p`` (Claude subscription, no API key).
* ``codex``       shells out to ``codex exec`` (ChatGPT subscription, no API key).

The CLI backends run one-shot with all tools disabled and read-only sandboxes;
they receive only the prompt. Summaries are best-effort: on any failure the
note is still written with the verbatim caption and transcript.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from shortform_notes.config import Settings

logger = logging.getLogger(__name__)

CLI_TIMEOUT_SECONDS = 180

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Short descriptive title, max 8 words, no hashtags, no emoji"},
        "summary": {"type": "string", "description": "2-4 sentences: what the video is about and its main point"},
        "takeaways": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3-6 concrete, specific takeaways: exact quantities, steps, names, numbers when present",
        },
    },
    "required": ["title", "summary", "takeaways"],
    "additionalProperties": False,
}


class SummaryError(Exception):
    """A backend failed; the caller degrades to a caption-derived title."""


@dataclass(frozen=True)
class Summary:
    title: str
    summary: str
    takeaways: tuple[str, ...]


def build_prompt(audience: str) -> str:
    return (
        f"You are turning a short social-media video into a personal note for {audience}. "
        "You are given the video's caption and/or spoken transcript. Return JSON with keys "
        "title (max 8 words, no hashtags, no emoji), summary (2-4 sentences: what the video is "
        "about and its main point) and takeaways (3-6 concrete, specific bullets: exact "
        "quantities, steps, names, numbers when present).\n"
        "Rules: never invent details that are not in the caption or transcript. If the video is "
        "a recipe, the takeaways must list every ingredient with amounts and the steps in order. "
        "Plain text only."
    )


def _user_message(caption: str | None, transcript: str | None) -> str:
    return f"Caption:\n{caption or '(none)'}\n\nTranscript:\n{transcript or '(none)'}"


def _cli_prompt(caption: str | None, transcript: str | None, settings: Settings) -> str:
    """Single-string prompt for agent CLIs: instructions, schema and content, JSON-only reply."""
    return (
        f"{build_prompt(settings.audience)}\n\n"
        f"Reply with ONLY a JSON object matching this schema, no prose, no code fences:\n"
        f"{json.dumps(SUMMARY_SCHEMA)}\n\n{_user_message(caption, transcript)}"
    )


def extract_json(text: str) -> dict:
    """Lenient parse for CLI output: strips code fences, then takes the outermost ``{...}``."""
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=re.I)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise SummaryError(f"no JSON object in model output: {text[:120]!r}")
    return json.loads(cleaned[start : end + 1])


def _coerce(data: dict, fallback_title: str) -> Summary:
    takeaways = tuple(str(t).strip() for t in data.get("takeaways") or [] if str(t).strip())
    return Summary(
        title=(data.get("title") or fallback_title).strip(),
        summary=(data.get("summary") or "").strip(),
        takeaways=takeaways,
    )


# API backends


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
        raise SummaryError("model declined to summarize this video")
    text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
    return json.loads(text or "{}")


# Coding-agent CLI backends


async def _run_cli(argv: list[str], stdin_text: str) -> str:
    """Run a CLI with the prompt on stdin; return stdout. Raises SummaryError on failure."""
    if shutil.which(argv[0]) is None:
        raise SummaryError(f"`{argv[0]}` is not on PATH")
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin_text.encode()), timeout=CLI_TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
        raise SummaryError(f"`{argv[0]}` timed out after {CLI_TIMEOUT_SECONDS}s") from None
    if proc.returncode != 0:
        raise SummaryError(f"`{argv[0]}` exited {proc.returncode}: {err.decode(errors='replace')[:300]}")
    return out.decode(errors="replace")


def claude_code_argv(settings: Settings) -> list[str]:
    """``claude -p`` one-shot: JSON envelope, every tool disabled, nothing persisted."""
    argv = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--tools",
        "",
        "--disable-slash-commands",
        "--no-session-persistence",
    ]
    if settings.claude_code_model:
        argv += ["--model", settings.claude_code_model]
    return argv


async def _summarize_claude_code(caption: str | None, transcript: str | None, settings: Settings) -> dict:
    raw = await _run_cli(claude_code_argv(settings), _cli_prompt(caption, transcript, settings))
    envelope = json.loads(raw)
    if envelope.get("is_error"):
        raise SummaryError(f"claude -p reported an error: {str(envelope.get('result'))[:200]}")
    return extract_json(str(envelope.get("result") or ""))


def codex_argv(settings: Settings, last_message_path: str) -> list[str]:
    """``codex exec`` one-shot: read-only sandbox, no approval prompts, final message to a file."""
    argv = [
        "codex",
        "exec",
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "never",
        "--output-last-message",
        last_message_path,
    ]
    if settings.codex_model:
        argv += ["--model", settings.codex_model]
    return argv + ["-"]  # "-" = read the prompt from stdin


async def _summarize_codex(caption: str | None, transcript: str | None, settings: Settings) -> dict:
    with tempfile.TemporaryDirectory(prefix="shortform-notes-codex-") as tmpdir:
        last_message = Path(tmpdir) / "last.txt"
        stdout = await _run_cli(codex_argv(settings, str(last_message)), _cli_prompt(caption, transcript, settings))
        text = last_message.read_text() if last_message.exists() else stdout
    return extract_json(text)


# Names, not function objects, so the lookup is late-bound (tests patch these).
_BACKENDS = {
    "openai": "_summarize_openai",
    "anthropic": "_summarize_anthropic",
    "claude-code": "_summarize_claude_code",
    "codex": "_summarize_codex",
}


async def summarize(
    caption: str | None, transcript: str | None, settings: Settings, title_hint: str | None = None
) -> Summary:
    """Return a Summary; degrades to a caption-derived title when the LLM step fails or is off."""
    fallback_title = (title_hint or caption or transcript or "Reel").split("\n")[0][:60]
    backend_name = _BACKENDS.get(settings.summary_provider)
    if backend_name is None:
        return Summary(fallback_title, "", ())
    backend = globals()[backend_name]
    try:
        data = await backend(caption, transcript, settings)
    except Exception as exc:  # noqa: BLE001 (summary is best-effort; the verbatim note is still written)
        logger.warning("summary failed (%s): %s", settings.summary_provider, exc)
        return Summary(fallback_title, "", ())
    return _coerce(data, fallback_title)
