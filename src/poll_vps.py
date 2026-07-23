#!/usr/bin/env python3
"""Poll VPS until clip render completes."""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import httpx_get_json_with_retry

SETUP_PHASES = frozenset({"queued", "clips"})
CLIP_STALL_SEC = 1800  # clip extraction should show steady progress
POST_CLIP_STALL_SEC = 7200  # concat/mux may run long without clips_ready updates


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()

    base = os.environ.get("VPS_URL", "").rstrip("/")
    secret = os.environ.get("VPS_SECRET", "")
    if not base or not secret:
        sys.exit("Set VPS_URL and VPS_SECRET")

    headers = {"Authorization": f"Bearer {secret}"}
    deadline = time.time() + args.timeout
    last_ready = -1
    last_progress_at = time.time()
    last_phase = ""

    while time.time() < deadline:
        try:
            data = httpx_get_json_with_retry(
                f"{base}/runs/{args.run_id}/status",
                headers=headers,
                timeout=60.0,
                retries=3,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"poll error (retrying): {exc}", flush=True)
            time.sleep(args.interval)
            continue

        status = data.get("status")
        ready = int(data.get("clips_ready", data.get("images_ready", 0)))
        total = int(data.get("total_scenes", 0))
        phase = data.get("phase", "")
        current_scene = data.get("current_scene", "")
        print(
            f"status={status} phase={phase} clips={ready}/{total} scene={current_scene}",
            flush=True,
        )

        if phase != last_phase:
            if phase not in SETUP_PHASES:
                last_progress_at = time.time()
            last_phase = phase

        if status == "complete":
            return

        if status == "failed":
            err = data.get("error") or "unknown error"
            sys.exit(f"VPS job failed: {err}")

        if ready > last_ready:
            last_ready = ready
            last_progress_at = time.time()
        elif status == "running":
            stall_limit = CLIP_STALL_SEC if phase in SETUP_PHASES else POST_CLIP_STALL_SEC
            stall_sec = time.time() - last_progress_at
            if phase in SETUP_PHASES and ready > 0 and stall_sec > stall_limit:
                sys.exit(
                    f"VPS job stalled at {ready}/{total} clips for {int(stall_sec // 60)}+ minutes"
                )
            if phase not in SETUP_PHASES and stall_sec > stall_limit:
                sys.exit(
                    f"VPS job stalled in {phase or 'post-clip'} phase for {int(stall_sec // 60)}+ minutes"
                )

        updated_at = data.get("updated_at")
        if updated_at:
            try:
                ts = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
                fresh_sec = 180 if phase in SETUP_PHASES else 3600
                if (datetime.now(timezone.utc) - ts).total_seconds() < fresh_sec:
                    last_progress_at = time.time()
            except ValueError:
                pass

        time.sleep(args.interval)

    sys.exit(f"Timeout after {args.timeout}s waiting for run {args.run_id}")


if __name__ == "__main__":
    main()
