"""Azure Neural TTS — per-chunk Speech SDK synthesis (True Crime pattern)."""

from __future__ import annotations

import os
import re
import xml.sax.saxutils
from pathlib import Path
from typing import Any

# Microsoft-documented express-as styles only.
_ROLE_STYLES: dict[str, str] = {
    "narration": "documentary-narration",
    "twist": "newscast-formal",
    "quote": "serious",
    "quote_male": "serious",
    "quote_female": "empathetic",
    "authority": "newscast-formal",
    "witness": "empathetic",
    "outro": "friendly",
}

_STYLE_VOICE_PREFIXES = (
    "en-US-Ava",
    "en-US-Andrew",
    "en-US-Guy",
    "en-US-Jenny",
    "en-US-Aria",
    "en-US-Christopher",
    "en-US-Davis",
    "en-US-Jane",
    "en-US-Brian",
    "en-US-Emma",
    "en-US-Eric",
    "en-US-Steffan",
)


def azure_configured() -> bool:
    return bool(os.environ.get("AZURE_SPEECH_KEY", "").strip() and os.environ.get("AZURE_SPEECH_REGION", "").strip())


def _style_degree() -> float:
    raw = os.environ.get("TTS_AZURE_STYLE_DEGREE", "0.92")
    try:
        val = float(raw)
    except ValueError:
        val = 0.92
    return max(0.5, min(1.2, val))


def _voice_supports_express_as(voice: str) -> bool:
    return any(voice.startswith(prefix) for prefix in _STYLE_VOICE_PREFIXES)


def _role_style(voice: str, role: str) -> str | None:
    if not _voice_supports_express_as(voice):
        return None
    return _ROLE_STYLES.get(role, "documentary-narration")


def _escape_ssml(text: str) -> str:
    return xml.sax.saxutils.escape(text)


def build_ssml(chunk: dict[str, Any], *, default_voice: str) -> str:
    """Build one Azure SSML document for a single prosody/voice chunk."""
    voice = str(chunk.get("voice") or default_voice)
    role = str(chunk.get("role", "narration"))
    rate = str(chunk.get("rate", "0%"))
    pitch = str(chunk.get("pitch", "0%"))
    volume = str(chunk.get("volume", "0%"))
    pause_ms = int(chunk.get("pause_ms", 0))
    text = str(chunk.get("text", "")).strip()
    if not text:
        return ""

    lang_match = re.match(r"([a-z]{2}-[A-Z]{2})", voice)
    lang = lang_match.group(1) if lang_match else "en-US"

    body = _escape_ssml(text)
    if pause_ms > 0:
        body = f'<break time="{pause_ms}ms"/>{body}'

    inner = (
        f'<prosody rate="{rate}" pitch="{pitch}" volume="{volume}">'
        f"<s>{body}</s></prosody>"
    )
    style = _role_style(voice, role)
    if style:
        inner = (
            f'<mstts:express-as style="{style}" styledegree="{_style_degree():.2f}">'
            f"{inner}</mstts:express-as>"
        )

    return (
        '<speak version="1.0" '
        'xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="https://www.w3.org/2001/mstts" '
        f'xml:lang="{lang}">'
        f'<voice name="{voice}">{inner}</voice></speak>'
    )


def is_transient_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(s in msg for s in ("timeout", "connect", "network", "503", "502", "429", "throttl"))


def probe_duration(path: Path) -> float:
    import subprocess

    if not path.exists() or path.stat().st_size == 0:
        return 0.5
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return max(0.5, float(result.stdout.strip()))


def synthesize_chunk(chunk: dict[str, Any], dest: Path, *, default_voice: str) -> None:
    """Synthesize one chunk to MP3 via Azure Speech SDK."""
    import azure.cognitiveservices.speech as speechsdk

    ssml = build_ssml(chunk, default_voice=default_voice)
    if not ssml:
        raise ValueError("Empty SSML")

    key = os.environ["AZURE_SPEECH_KEY"]
    region = os.environ["AZURE_SPEECH_REGION"]
    speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio24Khz160KBitRateMonoMp3
    )

    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
    result = synthesizer.speak_ssml_async(ssml).get()
    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        details = result.cancellation_details
        msg = details.error_details if details else str(result.reason)
        raise RuntimeError(f"Azure TTS failed: {msg}")

    audio = result.audio_data
    if not audio:
        raise RuntimeError("Azure TTS returned empty audio")

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(audio)


def estimate_ssml_characters(chunks: list[dict[str, Any]]) -> int:
    return sum(len(str(c.get("text", ""))) for c in chunks)


def quota_warning(char_count: int, *, monthly_limit: int = 500_000) -> str | None:
    videos_at_size = monthly_limit // max(char_count, 1)
    if char_count > 20_000:
        return f"High TTS usage: ~{char_count:,} chars this run (~{videos_at_size} videos/month at 500k shared quota)"
    return None
