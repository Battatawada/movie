#!/usr/bin/env python3
"""Build thumbnail.png from thumbnail.json (Wikimedia photo + text overlay)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from thumbnail_builder import build_thumbnail_from_meta


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("output"))
    args = parser.parse_args()

    meta_path = args.input / "thumbnail.json"
    if not meta_path.exists():
        sys.exit(f"Missing {meta_path}")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    out = build_thumbnail_from_meta(meta, args.input)
    print(f"Wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
