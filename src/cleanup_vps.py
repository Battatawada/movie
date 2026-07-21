#!/usr/bin/env python3
"""Remove movie library + run artifacts from VPS after a successful pipeline."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import load_json


def _delete(client, url: str, headers: dict[str, str], label: str) -> None:
    resp = client.delete(url, headers=headers)
    if resp.status_code >= 400:
        sys.exit(f"VPS {label} cleanup failed: {resp.status_code} {resp.text}")
    data = resp.json()
    print(f"{label}: {data.get('status', data)}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("output"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--movie-slug", default=None)
    parser.add_argument("--skip-movie", action="store_true")
    parser.add_argument("--skip-run", action="store_true")
    args = parser.parse_args()

    base = os.environ.get("VPS_URL", "").rstrip("/")
    secret = os.environ.get("VPS_SECRET", "")
    if not base or not secret:
        sys.exit("Set VPS_URL and VPS_SECRET")

    meta_path = args.input / "metadata.json"
    if not meta_path.exists():
        sys.exit(f"Missing metadata: {meta_path}")

    meta = load_json(meta_path)
    run_id = args.run_id or meta.get("run_id")
    movie_slug = args.movie_slug or meta.get("movie_slug")
    headers = {"Authorization": f"Bearer {secret}"}

    import httpx

    with httpx.Client(timeout=120.0) as client:
        if not args.skip_movie and movie_slug:
            _delete(client, f"{base}/movies/{movie_slug}", headers, f"movie:{movie_slug}")
        elif not args.skip_movie:
            print("No movie_slug in metadata — skipped movie cleanup", flush=True)

        if not args.skip_run and run_id:
            _delete(client, f"{base}/runs/{run_id}", headers, f"run:{run_id}")
        elif not args.skip_run:
            print("No run_id in metadata — skipped run cleanup", flush=True)


if __name__ == "__main__":
    main()
