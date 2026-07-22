"""Azure Speech TTS — SSML builder and synthesis."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import httpx

# Voices that support mstts:express-as (US neural). Irish/GB = prosody only.
EXPRESS_AS_VOICES = frozenset(
    {
        "en-US-ChristopherNeural",
        "en-US-GuyNeural",
        "en-US-AriaNeural",
        "en-US-JennyNeural",
        "en-US-AndrewMultilingualNeural",
        "en-US-EmmaMultilingualNeural",
    }
)

STYLE_MAP = {
    "narration": "newscast-casual",
    "quote": "serious",
    "quote_male": "serious",
    "quote_female": "empathetic",
    "authority": "newscast-formal",
    "witness": "empathetic",
    "outro": "friendly",
    "twist": "newscast-formal",
}


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def voice_supports_express_as(voice: str) -> bool:
    return voice in EXPRESS_AS_VOICES


def build_ssml(
    chunks: list[dict[str, Any]],
    *,
    default_voice: str,
    style_degree: float = 0.85,
) -> str:
    """Build a single <speak> SSML document from planned chunks."""
    parts: list[str] = []
    for chunk in chunks:
        text = str(chunk.get("text", "")).strip()
        if not text:
            continue
        voice = str(chunk.get("voice") or default_voice)
        role = str(chunk.get("role", "narration"))
        rate = str(chunk.get("rate", "0%"))
        pitch = str(chunk.get("pitch", "0%"))
        volume = str(chunk.get("volume", "0%"))
        pause_ms = int(chunk.get("pause_ms", 0))
        style = STYLE_MAP.get(role, "newscast-casual")
        inner = _xml_escape(text)
        prosody = f'<prosody rate="{rate}" pitch="{pitch}" volume="{volume}">{inner}</prosody>'
        if voice_supports_express_as(voice):
            body = (
                f'<mstts:express-as style="{style}" styledegree="{style_degree:.2f}">'
                f"{prosody}</mstts:express-as>"
            )
        else:
            body = prosody
        parts.append(f'<voice name="{voice}">{body}</voice>')
        if pause_ms > 0:
            parts.append(f'<break time="{pause_ms}ms"/>')
    if not parts:
        return ""
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="en-US">'
        + "".join(parts)
        + "</speak>"
    )


def synthesize_ssml(
    ssml: str,
    dest: Path,
    *,
    key: str | None = None,
    region: str | None = None,
    output_format: str = "audio-16khz-128kbitrate-mono-mp3",
    timeout: float = 120.0,
) -> Path:
    """Synthesize SSML to MP3 via Azure REST API."""
    if not ssml.strip():
        raise ValueError("Empty SSML")
    api_key = key or os.environ.get("AZURE_SPEECH_KEY", "").strip()
    api_region = region or os.environ.get("AZURE_SPEECH_REGION", "").strip()
    if not api_key or not api_region:
        raise RuntimeError("AZURE_SPEECH_KEY and AZURE_SPEECH_REGION required for Azure TTS")

    url = f"https://{api_region}.tts.speech.microsoft.com/cognitiveservices/v1"
    headers = {
        "Ocp-Apim-Subscription-Key": api_key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": output_format,
        "User-Agent": "RetroMovieArchive/1.0",
    }
    resp = httpx.post(url, content=ssml.encode("utf-8"), headers=headers, timeout=timeout)
    if resp.status_code == 429:
        raise RuntimeError("Azure TTS quota/rate limit exceeded (429)")
    if resp.status_code >= 400:
        detail = resp.text[:500]
        raise RuntimeError(f"Azure TTS failed ({resp.status_code}): {detail}")
    if not resp.content:
        raise RuntimeError("Azure TTS returned empty audio")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return dest


def estimate_ssml_characters(chunks: list[dict[str, Any]]) -> int:
    """Rough billable character count (spoken text only)."""
    return sum(len(str(c.get("text", ""))) for c in chunks)


def azure_configured() -> bool:
    return bool(os.environ.get("AZURE_SPEECH_KEY", "").strip() and os.environ.get("AZURE_SPEECH_REGION", "").strip())


def quota_warning(char_count: int, *, monthly_limit: int = 500_000) -> str | None:
    videos_at_size = monthly_limit // max(char_count, 1)
    if char_count > 20_000:
        return f"High TTS usage: ~{char_count:,} chars this run (~{videos_at_size} videos/month at 500k shared quota)"
    return None
