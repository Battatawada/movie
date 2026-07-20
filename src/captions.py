"""Karaoke captions (word-synced highlight) and SRT generation."""

from __future__ import annotations

import re
from pathlib import Path

# ASS colours are &HBBGGRR
WHITE = "&HFFFFFF&"
YELLOW = "&H00D7FF&"
RED = "&H0000FF&"
BLACK = "&H000000&"
HIGHLIGHT = "&H00D7FF&"  # gold-ish karaoke for retro recaps

EMILY = "en-IE-EmilyNeural"
ANDREW = "en-US-AndrewMultilingualNeural"
CHRISTOPHER = "en-US-ChristopherNeural"

# Common misconfiguration — en-US-EmilyNeural is not a valid edge-tts voice.
VOICE_ALIASES: dict[str, str] = {
    "en-US-EmilyNeural": EMILY,
    "en-US-Emily": EMILY,
}


def resolve_voice(name: str) -> str:
    return VOICE_ALIASES.get(name, name)

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Arial,42,{white},&H000000FF,{black},&H80000000,0,0,0,0,100,100,0,0,1,3,0,2,120,120,140,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ass_time(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    h, rem = divmod(cs, 360_000)
    m, rem = divmod(rem, 6_000)
    s, cs_rem = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs_rem:02d}"


# Max words visible at once — fixed blocks; highlight moves within the block (old karaoke feel)
KARAOKE_WINDOW = 7


def _display_word(raw: str) -> str:
    """Keep punctuation from source text; avoid ALL CAPS wall-of-text."""
    word = raw.strip()
    if not word:
        return ""
    word = re.sub(r"^[^\w'\"]+", "", word)
    return word


def attach_punctuation_from_text(words: list[dict], text: str) -> list[dict]:
    """edge-tts WordBoundary often drops punctuation — restore from script segment."""
    if not words or not text.strip():
        return words
    src_tokens = re.findall(r"\S+", text.strip())
    if len(src_tokens) == len(words):
        return [{**w, "text": src_tokens[i]} for i, w in enumerate(words)]

    out: list[dict] = []
    src_i = 0
    for w in words:
        core = re.sub(r"[^\w']", "", w.get("text", "")).lower()
        matched = w.get("text", "")
        while src_i < len(src_tokens):
            src = src_tokens[src_i]
            src_core = re.sub(r"[^\w']", "", src).lower()
            src_i += 1
            if core == src_core or (core and core in src_core):
                matched = src
                break
        out.append({**w, "text": matched})
    return out


def _chunk_bounds(length: int, active_idx: int, window: int = KARAOKE_WINDOW) -> tuple[int, int]:
    """Fixed 7-word chunks; text stays put while highlight moves word-to-word."""
    start = (active_idx // window) * window
    end = min(length, start + window)
    return start, end


def build_highlight_line(words: list[dict], active_idx: int) -> str:
    """Up to 7 words per block; active word highlighted, others white (no rotating window)."""
    start, end = _chunk_bounds(len(words), active_idx)
    parts: list[str] = []
    for j in range(start, end):
        token = _display_word(words[j].get("text", ""))
        if not token:
            continue
        if j == active_idx:
            parts.append(f"{{\\1c{HIGHLIGHT}}}{token}{{\\1c{WHITE}}}")
        else:
            parts.append(token)
    if not parts:
        return "..."
    # Soft line break near middle for long windows
    if len(parts) >= 5:
        mid = len(parts) // 2
        return " ".join(parts[:mid]) + "\\N" + " ".join(parts[mid:])
    return " ".join(parts)


def estimate_word_timings(text: str, duration: float) -> list[dict]:
    """Fallback when edge-tts returns no WordBoundary events."""
    tokens = re.findall(r"\S+", text.strip())
    if not tokens or duration <= 0:
        return []
    weights = [max(1, len(re.sub(r"[^\w']", "", t))) for t in tokens]
    total = sum(weights)
    t = 0.0
    out: list[dict] = []
    for token, weight in zip(tokens, weights):
        span = duration * (weight / total)
        out.append({"text": token, "start": round(t, 4), "end": round(t + span, 4)})
        t += span
    return out


def write_scene_karaoke_ass(words: list[dict], dest: Path, *, duration: float) -> Path | None:
    """ASS with one event per word — active word highlighted (red), rest white, black outline."""
    cleaned = [w for w in words if _display_word(w.get("text", ""))]
    if not cleaned:
        return None

    header = ASS_HEADER.format(white=WHITE, black=BLACK)
    lines = [header.rstrip()]

    for i, w in enumerate(cleaned):
        start = float(w["start"])
        if i + 1 < len(cleaned):
            end = float(cleaned[i + 1]["start"])
        else:
            end = max(float(w.get("end", start + 0.2)), start + 0.05)
        end = min(end, duration)
        if end <= start:
            end = start + 0.05
        text = build_highlight_line(cleaned, i)
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Karaoke,,0,0,0,,{text}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return dest


def format_srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def merge_srt_blocks(blocks: list[str], offsets: list[float]) -> str:
    """Merge per-scene SRT snippets with time offsets."""
    out: list[str] = []
    idx = 1
    for block, offset in zip(blocks, offsets):
        block = block.strip()
        if not block:
            continue
        for entry in re.split(r"\n\s*\n", block):
            lines = entry.strip().splitlines()
            if len(lines) < 2:
                continue
            times = lines[0]
            text = "\n".join(lines[1:])
            m = re.match(
                r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})",
                times,
            )
            if not m:
                continue

            def _parse(t: str) -> float:
                h, m_, rest = t.split(":")
                s, ms = rest.split(",")
                return int(h) * 3600 + int(m_) * 60 + int(s) + int(ms) / 1000

            start = _parse(m.group(1)) + offset
            end = _parse(m.group(2)) + offset
            out.append(f"{idx}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{text}\n")
            idx += 1
    return "\n".join(out).strip() + "\n"


def pick_narrator_voice(scene_index: int, voices: list[str], segment_text: str) -> str:
    """
    Mind In Minutes mix: Emily primary (~75%); Andrew every 4th scene
    (0-based indices 3, 7, 11…). Single-voice configs stay on that voice only.
    """
    if not voices:
        return EMILY
    if len(voices) == 1:
        return voices[0]
    primary = next((v for v in voices if "Emily" in v), voices[0])
    secondary = next((v for v in voices if "Andrew" in v), voices[-1])
    if scene_index % 4 == 3:
        return secondary
    return primary
