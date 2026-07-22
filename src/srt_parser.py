"""Parse SRT subtitles and resolve line ranges to ffmpeg timestamps."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SubtitleBlock:
    index: int
    start_sec: float
    end_sec: float
    text: str

    @property
    def start_ffmpeg(self) -> str:
        return _sec_to_ffmpeg(self.start_sec)

    @property
    def end_ffmpeg(self) -> str:
        return _sec_to_ffmpeg(self.end_sec)


def _srt_time_to_sec(raw: str) -> float:
    raw = raw.strip().replace(",", ".")
    parts = raw.split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid SRT timestamp: {raw}")
    h, m, s = parts
    return int(h) * 3600 + int(m) * 60 + float(s)


def _sec_to_ffmpeg(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def parse_srt(text: str) -> list[SubtitleBlock]:
    """Parse SRT content into indexed blocks (1-based index)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []

    blocks: list[SubtitleBlock] = []
    chunks = re.split(r"\n\s*\n", text)
    for chunk in chunks:
        lines = [ln.strip() for ln in chunk.split("\n") if ln.strip()]
        if len(lines) < 2:
            continue
        idx_line = lines[0]
        if not idx_line.isdigit():
            continue
        index = int(idx_line)
        timing = lines[1]
        if "-->" not in timing:
            continue
        start_raw, end_raw = [p.strip() for p in timing.split("-->", 1)]
        body = " ".join(lines[2:]).strip()
        body = re.sub(r"<[^>]+>", "", body)
        if not body:
            continue
        blocks.append(
            SubtitleBlock(
                index=index,
                start_sec=_srt_time_to_sec(start_raw),
                end_sec=_srt_time_to_sec(end_raw),
                text=body,
            )
        )
    return blocks


def load_srt(path: Path) -> list[SubtitleBlock]:
    return parse_srt(path.read_text(encoding="utf-8", errors="replace"))


def srt_to_llm_index(blocks: list[SubtitleBlock], *, max_chars: int = 120_000) -> str:
    """Compact numbered subtitle list for NotebookLM prompts."""
    lines: list[str] = []
    used = 0
    for b in blocks:
        line = f"{b.index}|{b.start_ffmpeg}|{b.text}"
        if used + len(line) + 1 > max_chars:
            lines.append(f"... truncated at line {b.index} ...")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def subtitle_index_bounds(blocks: list[SubtitleBlock]) -> tuple[int, int]:
    if not blocks:
        raise ValueError("No subtitle blocks")
    indices = [b.index for b in blocks]
    return min(indices), max(indices)


def clamp_subtitle_line(blocks: list[SubtitleBlock], line: int) -> int:
    """Snap a 1-based subtitle index to the nearest existing SRT index."""
    by_index = {b.index: b for b in blocks}
    if line in by_index:
        return line
    lo, hi = subtitle_index_bounds(blocks)
    line = max(lo, min(hi, line))
    if line in by_index:
        return line
    for candidate in range(line, hi + 1):
        if candidate in by_index:
            return candidate
    for candidate in range(line, lo - 1, -1):
        if candidate in by_index:
            return candidate
    raise ValueError("No subtitle blocks")


def normalize_subtitle_range(
    blocks: list[SubtitleBlock],
    start_line: int,
    end_line: int,
) -> tuple[int, int]:
    """Clamp LLM-picked subtitle indices to valid SRT line numbers."""
    start_line = clamp_subtitle_line(blocks, start_line)
    end_line = clamp_subtitle_line(blocks, end_line)
    if end_line < start_line:
        end_line = clamp_subtitle_line(blocks, start_line + 2)
    return start_line, end_line


def resolve_line_range(
    blocks: list[SubtitleBlock],
    start_line: int,
    end_line: int,
    *,
    pad_start: float = 0.0,
    pad_end: float = 0.25,
    max_duration: float = 12.0,
) -> tuple[float, float]:
    """Map 1-based inclusive subtitle line numbers to clip start/end seconds."""
    start_line, end_line = normalize_subtitle_range(blocks, start_line, end_line)
    by_index = {b.index: b for b in blocks}
    if start_line not in by_index or end_line not in by_index:
        raise ValueError(f"Subtitle lines {start_line}-{end_line} not found in SRT")
    if end_line < start_line:
        raise ValueError(f"Invalid range {start_line}-{end_line}")

    start = by_index[start_line].start_sec + pad_start
    end = by_index[end_line].end_sec + pad_end
    if end <= start:
        end = start + 1.5
    if end - start > max_duration:
        end = start + max_duration
    return round(start, 3), round(end, 3)


def blocks_for_range(blocks: list[SubtitleBlock], start_line: int, end_line: int) -> list[SubtitleBlock]:
    return [b for b in blocks if start_line <= b.index <= end_line]
