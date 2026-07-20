#!/usr/bin/env python3
"""One-time OAuth: open browser, sign in, print YOUTUBE_REFRESH_TOKEN."""

from __future__ import annotations

import argparse
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

# upload alone does NOT cover captions.insert — force-ssl is required for SRT upload.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Get YouTube OAuth refresh token")
    parser.add_argument(
        "--secrets",
        type=Path,
        default=Path("client_secret_846784737327-rvjskqqc4dhm1d26v6j5udrnoiko19o3.apps.googleusercontent.com.json"),
        help="OAuth client JSON downloaded from Google Cloud Console",
    )
    args = parser.parse_args()
    if not args.secrets.exists():
        raise SystemExit(f"Missing {args.secrets} — download Desktop OAuth JSON from GCP Credentials")

    flow = InstalledAppFlow.from_client_secrets_file(str(args.secrets), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    if not creds.refresh_token:
        raise SystemExit(
            "No refresh_token returned. Revoke app at "
            "https://myaccount.google.com/permissions and re-run."
        )
    print("YOUTUBE_REFRESH_TOKEN=", creds.refresh_token)


if __name__ == "__main__":
    main()
