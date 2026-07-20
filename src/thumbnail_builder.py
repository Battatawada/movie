"""Fetch Wikimedia still + compose YouTube thumbnail with text overlays."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


def _wiki_search_titles(query: str, limit: int = 5) -> list[str]:
    params = urllib.parse.urlencode({
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": limit,
        "format": "json",
    })
    url = f"https://en.wikipedia.org/w/api.php?{params}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [item["title"] for item in data.get("query", {}).get("search", [])]


def _wiki_page_image_url(title: str) -> str | None:
    params = urllib.parse.urlencode({
        "action": "query",
        "titles": title,
        "prop": "pageimages",
        "pithumbsize": 1280,
        "format": "json",
    })
    url = f"https://en.wikipedia.org/w/api.php?{params}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        thumb = (page.get("thumbnail") or {}).get("source")
        if thumb:
            return thumb
    return None


def fetch_thumbnail_image(meta: dict[str, Any], dest: Path) -> Path:
    """Download best-effort Wikimedia/Wikipedia lead image."""
    queries = [
        str(meta.get("image_search_query", "")).strip(),
        str(meta.get("fallback_search_query", "")).strip(),
        str(meta.get("topic", "")).strip(),
    ]
    img_url: str | None = None
    for q in queries:
        if not q:
            continue
        for title in _wiki_search_titles(q):
            img_url = _wiki_page_image_url(title)
            if img_url:
                break
        if img_url:
            break
    if not img_url:
        raise RuntimeError(f"No Wikimedia image found for queries: {queries}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(img_url, headers={"User-Agent": "RetroMovieArchive/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        dest.write_bytes(resp.read())
    return dest


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 6:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return 255, 255, 255


def _load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        p = Path(path)
        if p.exists():
            return ImageFont.truetype(str(p), size=size)
    return ImageFont.load_default()


def compose_thumbnail(meta: dict[str, Any], src: Path, dest: Path, *, size: tuple[int, int] = (1280, 720)) -> Path:
    """Crop to 16:9, dark gradient, bold title + subtitle."""
    img = Image.open(src).convert("RGB")
    img = _crop_center_16_9(img, size)

    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for y in range(int(size[1] * 0.45), size[1]):
        alpha = int(200 * (y - size[1] * 0.45) / (size[1] * 0.55))
        draw.line([(0, y), (size[0], y)], fill=(0, 0, 0, min(220, alpha)))

    base = img.convert("RGBA")
    base = Image.alpha_composite(base, overlay)

    draw = ImageDraw.Draw(base)
    title = str(meta.get("overlay_title") or meta.get("title", "RECAP"))[:24].upper()
    subtitle = str(meta.get("overlay_subtitle") or "RECAP")[:16].upper()
    text_color = _hex_to_rgb(str(meta.get("text_color", "#FFFFFF")))
    accent = _hex_to_rgb(str(meta.get("accent_color", "#FFD700")))

    title_font = _load_font(72)
    sub_font = _load_font(44)

    tw, th = draw.textbbox((0, 0), title, font=title_font)[2:]
    sw, sh = draw.textbbox((0, 0), subtitle, font=sub_font)[2:]
    tx = (size[0] - tw) // 2
    ty = size[1] - th - sh - 80
    sx = (size[0] - sw) // 2
    sy = ty + th + 12

    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
        draw.text((tx + dx, ty + dy), title, font=title_font, fill=(0, 0, 0))
        draw.text((sx + dx, sy + dy), subtitle, font=sub_font, fill=(0, 0, 0))
    draw.text((tx, ty), title, font=title_font, fill=text_color)
    draw.text((sx, sy), subtitle, font=sub_font, fill=accent)

    dest.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(dest, format="PNG", optimize=True)
    return dest


def _crop_center_16_9(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    target_ratio = size[0] / size[1]
    w, h = img.size
    current = w / h
    if current > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    return img.resize(size, Image.Resampling.LANCZOS)


def parse_thumbnail_json(raw: str) -> dict[str, Any]:
    from common import extract_json_blocks

    blocks = extract_json_blocks(raw)
    for block in blocks:
        if isinstance(block, dict) and block.get("overlay_title"):
            return block
    raise ValueError("No thumbnail JSON in NotebookLM response")


def build_thumbnail_from_meta(meta: dict[str, Any], output_dir: Path) -> Path:
    work = output_dir / "_thumb_work"
    work.mkdir(parents=True, exist_ok=True)
    raw_img = work / "source.jpg"
    fetch_thumbnail_image(meta, raw_img)
    out = output_dir / "thumbnail.png"
    compose_thumbnail(meta, raw_img, out)
    return out
