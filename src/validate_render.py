#!/usr/bin/env python3
"""Validate final render duration against scene narration timings."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import load_json


def probe_duration(path: Path) -> float:
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
    return float(result.stdout.strip())


def expected_duration(input_dir: Path) -> float:
    durations = load_json(input_dir / "scene_durations.json")
    total = sum(float(row["duration_sec"]) for row in durations)
    end_meta_path = input_dir / "end_card.json"
    end_audio = input_dir / "end_card.mp3"
    if end_meta_path.exists() and end_audio.exists():
        end_meta = load_json(end_meta_path)
        if end_meta.get("enabled", True):
            total += float(end_meta.get("duration_sec", probe_duration(end_audio)))
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("output"))
    parser.add_argument("--video", type=Path, default=None)
    parser.add_argument("--tolerance", type=float, default=0.95)
    args = parser.parse_args()

    video = args.video or (args.input / "final_video.mp4")
    if not video.exists():
        sys.exit(f"Missing video: {video}")

    expected = expected_duration(args.input)
    actual = probe_duration(video)
    if actual < expected * args.tolerance:
        sys.exit(
            f"Render too short: {actual:.1f}s vs expected {expected:.1f}s "
            f"(>{args.tolerance:.0%} required)"
        )
    print(
        {
            "status": "ok",
            "actual_sec": round(actual, 1),
            "expected_sec": round(expected, 1),
        },
        flush=True,
    )


if __name__ == "__main__":
    main()
