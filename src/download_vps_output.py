#!/usr/bin/env python3
"""Download final video + thumbnail from VPS after render."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import httpx_download_with_retry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, default=Path("output"))
    args = parser.parse_args()

    base = os.environ.get("VPS_URL", "").rstrip("/")
    secret = os.environ.get("VPS_SECRET", "")
    if not base or not secret:
        sys.exit("Set VPS_URL and VPS_SECRET")

    headers = {"Authorization": f"Bearer {secret}"}
    args.output.mkdir(parents=True, exist_ok=True)

    for name in ("final_video.mp4", "thumbnail.png"):
        dest = args.output / name
        httpx_download_with_retry(
            f"{base}/runs/{args.run_id}/output/{name}",
            dest,
            headers=headers,
            timeout=600.0,
        )
        print(f"Downloaded {dest} ({dest.stat().st_size} bytes)", flush=True)

    if not (args.output / "final_video.mp4").exists():
        sys.exit("Missing final_video.mp4 from VPS")


if __name__ == "__main__":
    main()
