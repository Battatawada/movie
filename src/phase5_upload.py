#!/usr/bin/env python3
"""Phase 5: YouTube upload (step 9) using SEO from step 8."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from common import CONFIG, load_json, sanitize_seo_title

# Must match scopes granted in scripts/youtube_oauth_refresh.py
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


def tag_char_count(tags: list[str]) -> int:
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)


def build_description(seo: dict, meta: dict, rules: dict) -> str:
    body = (seo.get("description") or meta.get("topic") or "").strip()
    footer = (rules.get("seo") or {}).get("description_footer", "").strip()
    if footer and footer not in body:
        body = f"{body}\n\n{footer}" if body else footer
    return body


def merge_tags(seo: dict, rules: dict) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for tag in (seo.get("tags") or []) + (rules.get("seo") or {}).get("default_tags", []):
        key = str(tag).strip().lower()
        if key and key not in seen:
            seen.add(key)
            merged.append(str(tag).strip())
    return merged


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def build_upload_status(upload_rules: dict[str, Any]) -> dict[str, Any]:
    """YouTube videos.insert status — avoids Studio 'made for kids' / visibility surprises."""
    return {
        "privacyStatus": os.environ.get(
            "YOUTUBE_PRIVACY", upload_rules.get("privacy", "public")
        ),
        "embeddable": upload_rules.get("embeddable", True),
        "license": upload_rules.get("license", "youtube"),
        "publicStatsViewable": upload_rules.get("public_stats_viewable", True),
        "madeForKids": _env_bool(
            "YOUTUBE_MADE_FOR_KIDS", bool(upload_rules.get("made_for_kids", False))
        ),
        "selfDeclaredMadeForKids": _env_bool(
            "YOUTUBE_SELF_DECLARED_MADE_FOR_KIDS",
            bool(upload_rules.get("self_declared_made_for_kids", False)),
        ),
        "containsSyntheticMedia": _env_bool(
            "YOUTUBE_CONTAINS_SYNTHETIC_MEDIA",
            bool(upload_rules.get("contains_synthetic_media", False)),
        ),
    }


def probe_video_duration_sec(path: Path) -> float | None:
    import subprocess

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print(json.dumps({"video_duration": "skipped", "reason": "ffprobe not installed"}))
        return None
    if result.returncode != 0:
        print(json.dumps({"video_duration": "skipped", "reason": (result.stderr or "ffprobe failed")[:200]}))
        return None
    return float(result.stdout.strip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, default=Path("output/metadata.json"))
    parser.add_argument("--seo", type=Path, default=Path("output/youtube_seo.json"))
    parser.add_argument("--captions", type=Path, default=Path("output/captions.srt"))
    parser.add_argument("--thumbnail", type=Path, default=Path("output/thumbnail.png"))
    args = parser.parse_args()

    for key in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"):
        if not os.environ.get(key):
            sys.exit(f"Missing env {key}")

    rules = load_json(CONFIG / "channel_rules.json") if (CONFIG / "channel_rules.json").exists() else {}
    upload_rules = rules.get("upload", {})
    category = os.environ.get("YOUTUBE_CATEGORY_ID", upload_rules.get("category_id", "27"))
    upload_status = build_upload_status(upload_rules)

    seo = load_json(args.seo) if args.seo.exists() else {}
    meta = load_json(args.metadata) if args.metadata.exists() else {}

    title_max = int((rules.get("seo") or {}).get("title_max_chars", 65))
    title = sanitize_seo_title(
        seo.get("title") or meta.get("title") or meta.get("topic") or "Motivation Story",
        max_chars=title_max,
    )
    description = build_description(seo, meta, rules)
    tags = merge_tags(seo, rules)
    if tag_char_count(tags) > 500:
        tags = tags[:10]

    if not args.thumbnail.exists() or args.thumbnail.stat().st_size < 10_000:
        sys.exit(
            "Missing or too-small thumbnail.png — custom thumbnail is required before upload. "
            f"path={args.thumbnail} exists={args.thumbnail.exists()}"
        )

    duration_sec = probe_video_duration_sec(args.video)
    if duration_sec is not None:
        print(
            json.dumps(
                {
                    "video_duration_sec": round(duration_sec, 1),
                    "video_duration_min": round(duration_sec / 60, 1),
                }
            )
        )

    youtube = build_youtube()
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": str(category),
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en",
        },
        "status": upload_status,
    }

    print(json.dumps({"upload_status": upload_status, "category_id": category}))

    media = MediaFileUpload(str(args.video), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload {int(status.progress() * 100)}%")

    video_id = response["id"]
    print(
        json.dumps(
            {
                "video_id": video_id,
                "url": f"https://youtu.be/{video_id}",
                "title": title,
                "privacy": upload_status.get("privacyStatus"),
                "made_for_kids": upload_status.get("madeForKids"),
                "contains_synthetic_media": upload_status.get("containsSyntheticMedia"),
            }
        )
    )

    if args.captions.exists():
        try:
            cap_body = {
                "snippet": {
                    "videoId": video_id,
                    "language": "en",
                    "name": "English (auto)",
                    "isDraft": False,
                }
            }
            cap_media = MediaFileUpload(
                str(args.captions), mimetype="application/x-subrip", resumable=True
            )
            cap_req = youtube.captions().insert(
                part="snippet",
                body=cap_body,
                media_body=cap_media,
                sync=True,
            )
            cap_resp = cap_req.execute()
            print(json.dumps({"caption_id": cap_resp.get("id"), "language": "en"}))
        except Exception as exc:  # noqa: BLE001
            print(
                json.dumps(
                    {
                        "caption_upload": "failed",
                        "error": str(exc),
                        "fix": (
                            "Re-run scripts/youtube_oauth_refresh.py (needs youtube.force-ssl "
                            "scope), update YOUTUBE_REFRESH_TOKEN secret, re-upload captions "
                            "manually in YouTube Studio if needed."
                        ),
                    }
                ),
                file=sys.stderr,
            )
    else:
        print("No captions.srt — skipped caption upload")

    try:
        thumb_media = MediaFileUpload(str(args.thumbnail), mimetype="image/png", resumable=True)
        thumb_req = youtube.thumbnails().set(videoId=video_id, media_body=thumb_media)
        thumb_resp = thumb_req.execute()
        print(json.dumps({"thumbnail_upload": "ok", "items": len(thumb_resp.get("items", []))}))
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps({"thumbnail_upload": "failed", "error": str(exc)}),
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
