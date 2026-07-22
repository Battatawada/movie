#!/usr/bin/env python3
"""Delete a YouTube video by ID (uses same OAuth as phase5_upload)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def build_youtube():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        scopes=YOUTUBE_SCOPES,
    )
    return build("youtube", "v3", credentials=creds)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video_id", help="YouTube video ID to delete")
    args = parser.parse_args()

    for key in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"):
        if not os.environ.get(key):
            sys.exit(f"Missing env {key}")

    youtube = build_youtube()
    try:
        youtube.videos().delete(id=args.video_id).execute()
    except Exception as exc:  # noqa: BLE001
        if "videoNotFound" in str(exc) or "404" in str(exc):
            print(f"video_id={args.video_id} already gone")
            return
        raise
    print(f"deleted video_id={args.video_id}")


if __name__ == "__main__":
    main()
