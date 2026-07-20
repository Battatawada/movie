#!/usr/bin/env python3
"""
Fallback when `notebooklm login` crashes on "Navigation interrupted".

Crime niche: use a SEPARATE profile from Psychology/Niche default.

Usage:
  python scripts/save_notebooklm_auth.py --profile retro
  notebooklm -p retro auth check --test
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

NOTEBOOKLM_URL = "https://notebooklm.google.com/"


def main() -> int:
    parser = argparse.ArgumentParser(description="Save NotebookLM Playwright auth for a profile")
    parser.add_argument(
        "--profile",
        default="retro",
        help="notebooklm-py profile name (default: retro — separate from crime/psychology)",
    )
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
        from notebooklm.paths import get_browser_profile_dir, get_storage_path
    except ImportError:
        print("Install: pip install notebooklm-py[browser] && playwright install chromium", file=sys.stderr)
        return 1

    # notebooklm-py resolves profile via NOTEBOOKLM_PROFILE / config; set env for path helpers
    import os

    os.environ["NOTEBOOKLM_PROFILE"] = args.profile

    profile = get_browser_profile_dir()
    storage = get_storage_path()
    storage.parent.mkdir(parents=True, exist_ok=True)

    print(f"Profile name: {args.profile}")
    print(f"Browser dir: {profile}")
    print(f"Saving to: {storage}")
    print("Open browser and sign in with the RETRO MOVIE ARCHIVE Google account.")
    print("Opening browser...")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--password-store=basic"],
            ignore_default_args=["--enable-automation"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(NOTEBOOKLM_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            if "interrupted" not in str(exc).lower():
                raise
            print(f"Note: redirect during load ({exc.__class__.__name__}), continuing...")

        input("\nWhen NotebookLM homepage is visible (retro account), press ENTER to save auth... ")

        if "notebooklm.google.com" not in page.url:
            print(f"Warning: URL is {page.url}", file=sys.stderr)
            if input("Save anyway? [y/N] ").strip().lower() != "y":
                context.close()
                return 1

        context.storage_state(path=str(storage))
        storage.chmod(0o600)
        context.close()

    print(f"Saved: {storage}")
    print(f"Verify: notebooklm -p {args.profile} auth check --test")
    print("Then: .\\scripts\\export_notebooklm_secret.ps1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
