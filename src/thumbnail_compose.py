"""Post-compose thumbnail text overlay (Layer 2).

Layer 1 (Wikimedia still fetch) lives in thumbnail_builder.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from thumbnail_builder import compose_thumbnail, fetch_thumbnail_image

__all__ = ["compose_thumbnail", "fetch_thumbnail_image", "build_thumbnail_from_meta"]


def build_thumbnail_from_meta(meta: dict[str, Any], output_dir: Path) -> Path:
    work = output_dir / "_thumb_work"
    work.mkdir(parents=True, exist_ok=True)
    raw_img = work / "source.jpg"
    fetch_thumbnail_image(meta, raw_img)
    out = output_dir / "thumbnail.png"
    compose_thumbnail(meta, raw_img, out)
    return out
