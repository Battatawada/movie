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

        if status == "complete":
            return

        if status == "failed":
            err = data.get("error") or "unknown error"
            sys.exit(f"VPS job failed: {err}")

        if ready > last_ready:
            last_ready = ready
            last_progress_at = time.time()
        elif status == "running" and ready > 0:
            stall_sec = time.time() - last_progress_at
            if stall_sec > 1800:
                sys.exit(
                    f"VPS job stalled at {ready}/{total} clips for {int(stall_sec // 60)}+ minutes"
                )

        updated_at = data.get("updated_at")
        if updated_at:
            try:
                ts = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
                if (datetime.now(timezone.utc) - ts).total_seconds() < 180:
                    last_progress_at = time.time()
            except ValueError:
                pass

        time.sleep(args.interval)

    sys.exit(f"Timeout after {args.timeout}s waiting for run {args.run_id}")


if __name__ == "__main__":
    main()
