#!/usr/bin/env python3
"""Upload pipeline inputs to VPS and trigger clip render."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import append_github_output, httpx_post_json_with_retry, load_json, new_run_id, validate_scene_clips

INPUT_FILES = (
    "metadata.json",
    "scene_clips.json",
    "scene_durations.json",
    "narration.mp3",
    "script_segments.json",
    "word_timings.json",
    "captions.srt",
    "end_card.mp3",
    "end_card.json",
    "thumbnail.png",
    "pipeline.json",
)

_MIME_BY_SUFFIX = {
    ".json": "application/json",
    ".srt": "text/plain; charset=utf-8",
    ".mp3": "audio/mpeg",
    ".png": "image/png",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("output"))
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    base = os.environ.get("VPS_URL", "").rstrip("/")
    secret = os.environ.get("VPS_SECRET", "")
    if not base or not secret:
        sys.exit("Set VPS_URL and VPS_SECRET")

    run_id = args.run_id
    if not run_id and (args.input / "metadata.json").exists():
        run_id = load_json(args.input / "metadata.json").get("run_id")
    run_id = run_id or new_run_id()

    headers = {"Authorization": f"Bearer {secret}"}

    import httpx

    files: list[tuple[str, tuple[str, bytes, str]]] = []
    for name in INPUT_FILES:
        path = args.input / name
        if not path.exists() and name == "pipeline.json":
            src = Path(__file__).resolve().parents[1] / "config" / "pipeline.json"
            if src.exists():
                path = src
            else:
                continue
        if not path.exists():
            continue
        mime = _MIME_BY_SUFFIX.get(path.suffix.lower(), "application/octet-stream")
        files.append((name, (name, path.read_bytes(), mime)))

    required = {"metadata.json", "scene_clips.json", "scene_durations.json", "narration.mp3"}
    present = {f[0] for f in files}
    missing = required - present
    if missing:
        sys.exit(f"Missing required inputs for VPS: {sorted(missing)}")

    scene_clips = load_json(args.input / "scene_clips.json").get("scenes", [])
    scene_durations = load_json(args.input / "scene_durations.json")
    validate_scene_clips(scene_clips, scene_durations)

    with httpx.Client(timeout=300.0) as client:
        resp = client.post(
            f"{base}/runs/{run_id}/inputs",
            headers=headers,
            files=files,
        )
        if resp.status_code >= 400:
            body = (resp.text or resp.reason_phrase or "no response body").strip()
            total_bytes = sum(len(item[1][1]) for item in files)
            sys.exit(
                f"Input upload failed: {resp.status_code} {body} "
                f"(files={len(files)}, bytes={total_bytes})"
            )

        data = httpx_post_json_with_retry(
            f"{base}/generate",
            json_body={"run_id": run_id},
            headers=headers,
            timeout=180.0,
            retries=5,
        )

    append_github_output("run_id", run_id)
    print(f"run_id={run_id}")
    print(data)


if __name__ == "__main__":
    main()
