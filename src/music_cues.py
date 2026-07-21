"""Per-scene background music mood + volume from narration text."""

from __future__ import annotations

import re
from typing import Any

# Mood → base volume multiplier (applied to pipeline bg_music.volume)
MOOD_VOLUME: dict[str, float] = {
    "calm": 0.65,
    "mystery": 0.85,
    "emotional": 0.80,
    "tense": 1.15,
    "action": 1.30,
    "reveal": 1.20,
}

TENSE_WORDS = frozenset(
    {
        "chase", "gun", "fight", "attack", "scream", "run", "explosion", "kill",
        "murder", "blood", "danger", "panic", "escape", "crash", "shoot",
    }
)
ACTION_WORDS = frozenset(
    {"fight", "chase", "battle", "explodes", "crashes", "sprints", "attacks", "punches"}
)
MYSTERY_WORDS = frozenset(
    {"secret", "clue", "strange", "unknown", "disappear", "mystery", "hidden", "suspect"}
)
REVEAL_WORDS = frozenset(
    {
        "twist", "reveal", "truth", "actually", "turns out", "discover", "realizes",
        "finally", "secret", "confession", "plot", "ending",
    }
)
EMOTIONAL_WORDS = frozenset(
    {"love", "cries", "tear", "heart", "goodbye", "dies", "loss", "grief", "romance"}
)


def _word_hits(text: str, vocab: frozenset[str]) -> int:
    lower = text.lower()
    return sum(1 for w in vocab if w in lower)


def infer_music_mood(text: str, *, scene_index: int, total_scenes: int) -> str:
    """Pick dominant mood label for a narration beat."""
    if scene_index == 0:
        return "mystery"  # hook — low bed, tension building
    if scene_index >= max(1, total_scenes - 2):
        return "reveal"  # climax / ending explained

    scores = {
        "action": _word_hits(text, ACTION_WORDS) * 2,
        "tense": _word_hits(text, TENSE_WORDS),
        "reveal": _word_hits(text, REVEAL_WORDS) * 2,
        "emotional": _word_hits(text, EMOTIONAL_WORDS),
        "mystery": _word_hits(text, MYSTERY_WORDS),
    }
    if text.strip().endswith("?"):
        scores["mystery"] += 2
    if re.search(r"\b(but|however|suddenly|then)\b", text, re.I):
        scores["tense"] += 1

    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "calm"
    return best


def plan_music_cue(
    text: str,
    *,
    scene_index: int,
    total_scenes: int,
    base_volume: float = 0.12,
    mood_override: str | None = None,
) -> dict[str, Any]:
    mood = mood_override or infer_music_mood(text, scene_index=scene_index, total_scenes=total_scenes)
    mult = MOOD_VOLUME.get(mood, 0.85)
    # Hook scene: keep music lower so voice feels intimate and human
    if scene_index == 0:
        mult *= 0.75
    volume = round(min(0.20, max(0.05, base_volume * mult)), 3)
    return {
        "music_mood": mood,
        "music_volume": volume,
    }


def smooth_scene_volumes(cues: list[dict[str, Any]], *, max_step: float = 0.04) -> list[dict[str, Any]]:
    """Limit volume jumps between adjacent scenes for musical crossfades."""
    if not cues:
        return cues
    out = [dict(cues[0])]
    for i in range(1, len(cues)):
        prev = float(out[-1]["music_volume"])
        cur = float(cues[i]["music_volume"])
        if cur > prev + max_step:
            cur = prev + max_step
        elif cur < prev - max_step:
            cur = prev - max_step
        row = dict(cues[i])
        row["music_volume"] = round(cur, 3)
        out.append(row)
    return out
