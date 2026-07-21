"""TTS chunk planner — human-like prosody, quote detection, natural pauses."""

from __future__ import annotations

import re
from typing import Any

TENSION_WORDS = frozenset(
    {
        "killed", "murder", "death", "dead", "body", "blood", "confession",
        "twist", "reveal", "secret", "lied", "trap", "betray", "gun", "knife",
        "scream", "chase", "explosion",
    }
)
TWIST_WORDS = frozenset({"but", "however", "except", "instead", "until", "actually", "yet", "suddenly"})
EMOTIONAL_WORDS = frozenset({"love", "heart", "cries", "tear", "goodbye", "sorry", "afraid", "alone"})


def _default_voice_pool(pipeline: dict[str, Any]) -> dict[str, str]:
    pool = pipeline.get("tts_voice_pool") or {}
    voices = pipeline.get("tts_voices") or ["en-US-ChristopherNeural"]
    primary = voices[0] if isinstance(voices, list) else str(voices)
    return {
        "narrator": str(pool.get("narrator") or primary),
        "narrator_alt": str(pool.get("narrator_alt") or primary),
        "quote_male": str(pool.get("quote_male") or "en-US-GuyNeural"),
        "quote_female": str(pool.get("quote_female") or "en-US-AriaNeural"),
        "authority": str(pool.get("authority") or "en-US-GuyNeural"),
        "witness": str(pool.get("witness") or "en-US-JennyNeural"),
    }


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    cleaned: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Keep quoted dialogue as its own beat when embedded
        if '"' in part and not part.startswith('"'):
            pre, _, rest = part.partition('"')
            if pre.strip():
                cleaned.append(pre.strip())
            if rest:
                cleaned.append(f'"{rest}' if not rest.startswith('"') else rest)
        else:
            cleaned.append(part)
    return cleaned


def _is_quoted(sentence: str) -> bool:
    s = sentence.strip()
    return (s.startswith('"') and '"' in s[1:]) or (s.startswith("'") and "'" in s[1:])


def _quote_gender_hint(sentence: str) -> str:
    lower = sentence.lower()
    female_hints = (" she ", " her ", " mrs ", " ms ", " woman ", " girl ", " mother ", " daughter ")
    if any(h in f" {lower} " for h in female_hints):
        return "quote_female"
    return "quote_male"


def _pause_after_sentence(sentence: str, *, humanize: bool) -> int:
    if not humanize:
        return 120
    s = sentence.strip()
    if s.endswith("..."):
        return 520
    if s.endswith("?"):
        return 420
    if s.endswith("!"):
        return 400
    if s.endswith("."):
        return 340
    if s.endswith(","):
        return 200
    return 280


def _prosody_for_sentence(
    sentence: str,
    *,
    scene_index: int,
    sent_index: int,
    base_rate: str,
    is_hook_scene: bool,
    humanize: bool,
) -> dict[str, str]:
    lower = sentence.lower()
    words = re.findall(r"[a-z']+", lower)
    rate = base_rate
    pitch = "0%"
    volume = "0%"

    # Micro-variation so lines don't sound copy-pasted (human breath rhythm)
    if humanize and sent_index % 2 == 1:
        pitch = "+1%"
    elif humanize and sent_index % 3 == 2:
        pitch = "-1%"

    if is_hook_scene:
        rate = _adjust_rate(base_rate, -3)
        pitch = "-1%"
        volume = "+2%"

    # Short punchy lines — slightly quicker (how real narrators emphasize beats)
    word_count = len(words)
    if 3 <= word_count <= 8 and sent_index > 0:
        rate = _adjust_rate(rate, 2)

    if any(w in TENSION_WORDS for w in words):
        rate = _adjust_rate(rate, -4)
        pitch = "-2%"
        volume = "+1%"

    if any(w in EMOTIONAL_WORDS for w in words):
        rate = _adjust_rate(rate, -2)
        pitch = "+1%"

    if words and words[0] in TWIST_WORDS:
        rate = _adjust_rate(rate, -3)
        volume = "+3%"
        pitch = "-1%"

    if sentence.strip().endswith("?"):
        rate = _adjust_rate(rate, -2)
        pitch = "+2%"

    if _is_quoted(sentence) and humanize:
        rate = _adjust_rate(rate, -1)
        pitch = "+1%"

    return {"rate": rate, "pitch": pitch, "volume": volume}


def _adjust_rate(base: str, delta_pct: int) -> str:
    m = re.match(r"([+-]?\d+)%", str(base).strip())
    current = int(m.group(1)) if m else 0
    return f"{max(-20, min(15, current + delta_pct)):+d}%"


def _role_for_sentence(sentence: str, scene_index: int, sent_index: int) -> str:
    if _is_quoted(sentence):
        return _quote_gender_hint(sentence)
    words = re.findall(r"[a-z']+", sentence.lower())
    if words and words[0] in TWIST_WORDS:
        return "twist"
    return "narration"


def _voice_for_role(role: str, pool: dict[str, str]) -> str:
    return {
        "narration": pool["narrator"],
        "twist": pool["narrator"],
        "quote_male": pool["quote_male"],
        "quote_female": pool["quote_female"],
        "authority": pool["authority"],
        "witness": pool["witness"],
        "outro": pool["narrator"],
    }.get(role, pool["narrator"])


def plan_scene_chunks(
    text: str,
    *,
    scene_index: int,
    pipeline: dict[str, Any],
) -> list[dict[str, Any]]:
    """Split scene narration into prosody chunks for Azure SSML."""
    text = text.strip()
    if not text:
        return []

    pool = _default_voice_pool(pipeline)
    base_rate = str(pipeline.get("tts_rate", "-4%"))
    humanize = bool(pipeline.get("tts_humanize", True))
    is_hook = scene_index == 0
    chunks: list[dict[str, Any]] = []

    sentences = _split_sentences(text)
    for sent_i, sentence in enumerate(sentences):
        role = _role_for_sentence(sentence, scene_index, sent_i)
        prosody = _prosody_for_sentence(
            sentence,
            scene_index=scene_index,
            sent_index=sent_i,
            base_rate=base_rate,
            is_hook_scene=is_hook,
            humanize=humanize,
        )
        pause_ms = 0
        if sent_i > 0:
            pause_ms = _pause_after_sentence(sentences[sent_i - 1], humanize=humanize)
            if _is_quoted(sentence) and humanize:
                pause_ms = max(pause_ms, 280)  # breath before dialogue
        chunks.append({
            "text": sentence,
            "role": role,
            "voice": _voice_for_role(role, pool),
            "pause_ms": pause_ms,
            **prosody,
        })

    return chunks


def plan_outro_chunk(script: str, pipeline: dict[str, Any]) -> dict[str, Any]:
    pool = _default_voice_pool(pipeline)
    return {
        "text": script.strip(),
        "role": "outro",
        "voice": pool["narrator"],
        "rate": _adjust_rate(str(pipeline.get("tts_rate", "-4%")), 1),
        "pitch": "+1%",
        "volume": "+2%",
        "pause_ms": 0,
    }


def total_character_estimate(scenes: list[str], pipeline: dict[str, Any], *, end_card: str = "") -> int:
    total = 0
    for i, text in enumerate(scenes):
        chunks = plan_scene_chunks(text, scene_index=i, pipeline=pipeline)
        total += sum(len(c["text"]) for c in chunks)
    if end_card.strip():
        total += len(end_card.strip())
    return total
