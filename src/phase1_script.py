#!/usr/bin/env python3
"""
Phase 1 — Movie recap script + subtitle scene map

  1. Pick next movie from queue (VPS library)
  2. Load SRT transcript (VPS API or local)
  3. NotebookLM: ingest SRT → recap script (multi-part)
  4. NotebookLM: map each narration scene → subtitle line range
  5. Resolve timestamps from SRT (ground truth)
  6. YouTube SEO + thumbnail brief
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (
    CONFIG,
    append_topic_history,
    clean_script_for_tts,
    clips_to_scenes,
    estimate_scene_count,
    extract_json_blocks,
    extract_notebook_id,
    extract_source_id,
    fallback_seo,
    filter_topics_against_history,
    format_topic_history_for_prompt,
    is_transient_notebooklm_error,
    load_json,
    load_prompt,
    load_topic_history,
    new_run_id,
    notebooklm_json_with_retry,
    parse_numbered_topics,
    parse_seo_json,
    parse_total_parts,
    save_json,
    split_script_for_scenes,
    strip_markdown,
    strip_total_parts_header,
    topic_overlaps_history,
)
from srt_parser import SubtitleBlock, load_srt, parse_srt, resolve_line_range, srt_to_llm_index


def wait_sources(
    notebook_id: str,
    source_ids: list[str],
    *,
    timeout: int = 900,
    max_attempts: int = 5,
) -> None:
    import subprocess

    for idx, sid in enumerate(source_ids, start=1):
        print(f"  Waiting for source {idx}/{len(source_ids)} ({sid[:8]}...)", flush=True)
        last_err = ""
        for attempt in range(max_attempts):
            result = subprocess.run(
                [
                    "notebooklm", "source", "wait", sid,
                    "-n", notebook_id, "--timeout", str(timeout), "--interval", "3",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                break
            last_err = (result.stderr or result.stdout or "source wait failed").strip()
            if attempt + 1 < max_attempts and is_transient_notebooklm_error(last_err):
                time.sleep(20 * (attempt + 1))
                continue
            raise RuntimeError(f"Source {sid} failed: {last_err}")


def _parse_ask_response(raw_stdout: str) -> str:
    text = raw_stdout.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
            answer = data.get("answer") or data.get("text") or ""
            if isinstance(answer, str) and answer.strip():
                return answer.strip()
        except json.JSONDecodeError:
            pass
    return text


def ask(
    notebook_id: str,
    prompt: str,
    *,
    new: bool = False,
    retries: int = 4,
    request_timeout: int = 180,
) -> str:
    import subprocess

    prompt_file: Path | None = None
    if len(prompt) > 6000:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write(prompt)
        tmp.close()
        prompt_file = Path(tmp.name)

    cmd = [
        "notebooklm", "ask",
        *(["--prompt-file", str(prompt_file)] if prompt_file else [prompt]),
        "--notebook", notebook_id,
        "--request-timeout", str(request_timeout),
        "--json",
    ]
    if new:
        cmd.extend(["--new", "--yes"])

    last_err = ""
    for attempt in range(retries):
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if prompt_file:
            prompt_file.unlink(missing_ok=True)
        if result.returncode == 0:
            return _parse_ask_response(result.stdout)
        last_err = (result.stderr or result.stdout or "ask failed").strip()
        if attempt + 1 < retries and is_transient_notebooklm_error(last_err):
            time.sleep(15 * (attempt + 1))
            if len(prompt) > 6000:
                tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
                tmp.write(prompt)
                tmp.close()
                prompt_file = Path(tmp.name)
            continue
        raise RuntimeError(last_err)
    raise RuntimeError(last_err)


def collect_multipart_text(
    notebook_id: str, initial_prompt: str, continue_word: str = "Next", *, new: bool = False
) -> tuple[str, int]:
    first = ask(notebook_id, initial_prompt, new=new)
    total = parse_total_parts(first)
    chunks = [clean_script_for_tts(strip_total_parts_header(strip_markdown(first)))]
    for part_num in range(2, total + 1):
        print(f"  Story part {part_num}/{total}...", flush=True)
        cont = ask(notebook_id, continue_word)
        chunks.append(clean_script_for_tts(strip_total_parts_header(strip_markdown(cont))))
    return "\n\n".join(c for c in chunks if c), total


def fetch_srt_text(movie_slug: str, pipeline: dict[str, Any]) -> str:
    """Load SRT from VPS API or local movies dir."""
    local_root = Path(os.environ.get("LOCAL_MOVIES_DIR", CONFIG / "movies"))
    local_srt = local_root / movie_slug / "subtitles.srt"
    if local_srt.exists():
        print(f"  SRT from local: {local_srt}", flush=True)
        return local_srt.read_text(encoding="utf-8", errors="replace")

    base = os.environ.get("VPS_URL", "").rstrip("/")
    secret = os.environ.get("VPS_SECRET", "")
    if not base or not secret:
        raise RuntimeError(
            f"No SRT at {local_srt} and VPS_URL/VPS_SECRET not set. "
            "Place subtitles on VPS or set LOCAL_MOVIES_DIR."
        )
    from common import httpx_get_json_with_retry

    data = httpx_get_json_with_retry(
        f"{base}/movies/{movie_slug}/srt",
        headers={"Authorization": f"Bearer {secret}"},
        timeout=120.0,
    )
    content = data.get("content") or data.get("srt") or ""
    if not content:
        raise RuntimeError(f"VPS returned empty SRT for {movie_slug}")
    print(f"  SRT from VPS ({data.get('line_count', '?')} lines)", flush=True)
    return content


def pick_movie_from_queue(history: list[dict[str, Any]]) -> dict[str, Any]:
    queue = load_json(CONFIG / "movie_queue.json")
    movies = [m for m in queue.get("movies", []) if m.get("enabled", True)]
    for movie in movies:
        label = f"{movie.get('title', movie['slug'])} ({movie.get('year', '')})"
        topic = movie.get("topic") or label
        if topic_overlaps_history(topic, history) or topic_overlaps_history(label, history):
            print(f"  Skipping queued movie (already done): {label}", flush=True)
            continue
        return movie
    raise RuntimeError("No enabled movies left in movie_queue.json (all done or disabled).")


def pick_topic_from_notebook(
    notebook_id: str,
    history: list[dict[str, Any]],
) -> tuple[str, str]:
    """Return (topic_label, movie_slug) from NotebookLM topic list + queue match."""
    past_topics = format_topic_history_for_prompt(history)
    topics_prompt = load_prompt("topics_finding.txt").replace("{past_topics}", past_topics)
    topics_raw = ask(notebook_id, topics_prompt, new=True)
    parsed = parse_numbered_topics(topics_raw)
    kept, rejected = filter_topics_against_history(parsed, history)
    for t, reason in rejected:
        print(f"  Topic blocked: {t[:80]} — {reason}", flush=True)
    if not kept:
        raise RuntimeError("No fresh movie topics from NotebookLM")

    topics_list = "\n".join(f"{i}. {t}" for i, t in enumerate(kept[:10], 1))
    pick_prompt = load_prompt("pick_topic.txt").replace("{topics_list}", topics_list)
    topic = ask(notebook_id, pick_prompt, new=True).strip().splitlines()[0].strip()
    if topic_overlaps_history(topic, history):
        topic = kept[0]

    queue = load_json(CONFIG / "movie_queue.json")
    slug = _match_queue_slug(topic, queue.get("movies", []))
    if not slug:
        raise RuntimeError(
            f"Picked topic not in movie_queue.json: {topic}. "
            "Add the film to config/movie_queue.json with matching slug on VPS."
        )
    return topic, slug


def _match_queue_slug(topic: str, movies: list[dict[str, Any]]) -> str | None:
    norm = re.sub(r"[^a-z0-9]", "", topic.lower())
    for m in movies:
        title = str(m.get("title", ""))
        year = str(m.get("year", ""))
        slug = str(m.get("slug", ""))
        blob = re.sub(r"[^a-z0-9]", "", f"{title}{year}".lower())
        if blob and blob in norm:
            return slug
        if slug.replace("-", "") in norm:
            return slug
    return None


def build_scene_mapping_prompt(
    segments: list[str],
    blocks: list[SubtitleBlock],
    pipeline: dict[str, Any],
) -> str:
    scene_lines = "\n".join(
        f"Scene {i + 1}: {seg[:220]}{'...' if len(seg) > 220 else ''}"
        for i, seg in enumerate(segments)
    )
    return (
        load_prompt("scene_mapping.txt")
        .replace("{scene_count}", str(len(segments)))
        .replace("{narration_scenes}", scene_lines)
        .replace("{subtitle_index_sample}", srt_to_llm_index(blocks[:200]))
        .replace("{max_clip_sec}", str(pipeline.get("max_clip_source_sec", 8.0)))
    )


def parse_scene_mapping(raw: str, scene_count: int) -> list[dict[str, Any]]:
    blocks = extract_json_blocks(raw)
    for block in blocks:
        if isinstance(block, dict) and isinstance(block.get("scenes"), list):
            scenes = block["scenes"]
            if len(scenes) >= scene_count:
                return scenes[:scene_count]
    raise ValueError("No valid scene mapping JSON in NotebookLM response")


def resolve_scene_clips(
    mapping: list[dict[str, Any]],
    segments: list[str],
    blocks: list[SubtitleBlock],
    pipeline: dict[str, Any],
) -> list[dict[str, Any]]:
    pad_start = float(pipeline.get("clip_pad_start_sec", 0.0))
    pad_end = float(pipeline.get("clip_pad_end_sec", 0.3))
    max_dur = float(pipeline.get("max_clip_source_sec", 8.0))
    out: list[dict[str, Any]] = []
    last_end_line = 0

    for i, row in enumerate(mapping):
        sid = int(row.get("scene_id", i + 1))
        start_line = int(row["subtitle_start"])
        end_line = int(row["subtitle_end"])
        if start_line <= last_end_line:
            start_line = last_end_line + 1
        if end_line < start_line:
            end_line = start_line + 2
        start_sec, end_sec = resolve_line_range(
            blocks, start_line, end_line,
            pad_start=pad_start, pad_end=pad_end, max_duration=max_dur,
        )
        last_end_line = end_line
        text = segments[i] if i < len(segments) else ""
        out.append({
            "scene_id": sid,
            "narration": text,
            "subtitle_start": start_line,
            "subtitle_end": end_line,
            "start": start_sec,
            "end": end_sec,
            "start_ffmpeg": _sec_to_ffmpeg(start_sec),
            "end_ffmpeg": _sec_to_ffmpeg(end_sec),
        })
    return out


def _load_seed_config() -> dict[str, Any]:
    path = CONFIG / "seed_channels.json"
    if not path.exists():
        return {}
    return load_json(path)


def _collect_style_source_urls() -> tuple[list[str], list[str], list[str]]:
    """Return (channel_urls, video_urls, music_urls)."""
    data = _load_seed_config()
    channels: list[str] = []
    for ch in data.get("channels", []):
        u = str(ch.get("url", "")).strip()
        if u and "REPLACE" not in u:
            channels.append(u)
    videos: list[str] = []
    for item in data.get("sample_videos", []):
        if isinstance(item, dict):
            u = str(item.get("url", "")).strip()
        else:
            u = str(item).strip()
        if u and "REPLACE" not in u:
            videos.append(u)
    music: list[str] = []
    for item in data.get("music_references", []):
        u = str(item.get("url", "")).strip()
        if u and "REPLACE" not in u:
            music.append(u)
    return (
        list(dict.fromkeys(channels)),
        list(dict.fromkeys(videos)),
        list(dict.fromkeys(music)),
    )


def _collect_seed_urls() -> list[str]:
    ch, vid, mus = _collect_style_source_urls()
    return list(dict.fromkeys(ch + vid + mus))


def _default_style_notes() -> str:
    return (
        "- Hook in first 10s with stakes + curiosity\n"
        "- Calm confident narrator; spoil full plot\n"
        "- Thumbnail: one face, 2–4 word title, RECAP/EXPLAINED accent\n"
        "- Subtle cinematic ambient bed under voice (~12% volume)\n"
        "- SEO title: Movie Name + Recap / Ending Explained"
    )


def _ingest_style_sources(notebook_id: str, pipeline: dict[str, Any]) -> str:
    if not pipeline.get("ingest_style_channels", True):
        return _default_style_notes()

    channels, videos, music_urls = _collect_style_source_urls()
    all_urls = list(dict.fromkeys(channels + videos + music_urls))
    if not all_urls:
        print("  No seed channels configured — using default style brief", flush=True)
        return _default_style_notes()

    source_ids: list[str] = []
    delay = float(pipeline.get("source_add_delay_sec", 5))
    timeout = int(pipeline.get("source_request_timeout", 180))
    max_sources = int(pipeline.get("max_style_sources", 12))

    for i, url in enumerate(all_urls[:max_sources]):
        if i:
            time.sleep(delay)
        label = "channel" if url in channels else ("music" if url in music_urls else "video")
        print(f"  Adding style source ({label}) {i + 1}/{min(len(all_urls), max_sources)}...", flush=True)
        added = notebooklm_json_with_retry(
            "source", "add", url, "--notebook", notebook_id, "--request-timeout", str(timeout),
        )
        source_ids.append(extract_source_id(added))

    if source_ids:
        wait_sources(
            notebook_id,
            source_ids,
            timeout=int(pipeline.get("style_source_wait_timeout", pipeline.get("source_wait_timeout", 1200))),
        )

    notes_parts: list[str] = []

    print("[Style] Master brief (channels + subtitles)...", flush=True)
    master = ask(notebook_id, load_prompt("style_analysis.txt"), new=True, request_timeout=300)
    if master.strip():
        notes_parts.append("## Master style brief\n" + master.strip())

    for idx, url in enumerate(videos[:6], start=1):
        print(f"[Style] Per-video analysis {idx}/{len(videos[:6])}...", flush=True)
        vprompt = load_prompt("video_style_analysis.txt").replace("{video_url}", url)
        chunk = ask(notebook_id, vprompt, new=True, request_timeout=240)
        if chunk.strip():
            notes_parts.append(f"## Video {idx}\n{url}\n{chunk.strip()}")
        time.sleep(8)

    if music_urls:
        print("[Style] Music reference analysis...", flush=True)
        mus = ask(notebook_id, load_prompt("music_style_analysis.txt"), new=True, request_timeout=180)
        if mus.strip():
            notes_parts.append("## Music bed reference\n" + mus.strip())

    notes = "\n\n".join(notes_parts).strip() or _default_style_notes()
    return notes[:12000]


def _sec_to_ffmpeg(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 1: movie recap script + scene map")
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--pipeline", type=Path, default=CONFIG / "pipeline.json")
    parser.add_argument("--movie-slug", default=None, help="Override queue pick")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    run_id = new_run_id()
    pipeline = load_json(args.pipeline) if args.pipeline.exists() else {}
    niche = load_json(CONFIG / "niche.json") if (CONFIG / "niche.json").exists() else {}
    duration = int(pipeline.get("duration_minutes", 12))
    wpm = int(pipeline.get("words_per_minute", 145))
    continue_word = pipeline.get("continue_keyword", "Next")
    target_words = duration * wpm
    history = load_topic_history()
    thumbnail_meta = None

    if args.dry_run:
        movie_slug = "example-movie-1999"
        topic = "Example Movie (1999) — demo recap"
        script = (
            "In a quiet town, nothing seems wrong until one discovery changes everything. "
            "This is the story of how one choice spiraled into chaos."
        )
        blocks = parse_srt(
            "1\n00:00:01,000 --> 00:00:04,000\nHello world.\n\n"
            "2\n00:00:05,000 --> 00:00:08,000\nSomething happens.\n"
        )
        segments = split_script_for_scenes(clean_script_for_tts(script), 2)
        mapping = [
            {"scene_id": 1, "subtitle_start": 1, "subtitle_end": 1},
            {"scene_id": 2, "subtitle_start": 2, "subtitle_end": 2},
        ]
        scene_clips = resolve_scene_clips(mapping, segments, blocks, pipeline)
        seo = fallback_seo(topic)
        style_notes = _default_style_notes()
        story_parts = 1
        notebook_id = ""
    else:
        if args.movie_slug:
            queue = load_json(CONFIG / "movie_queue.json")
            movie = next((m for m in queue.get("movies", []) if m["slug"] == args.movie_slug), None)
            if not movie:
                sys.exit(f"Unknown --movie-slug: {args.movie_slug}")
            topic = movie.get("topic") or f"{movie['title']} ({movie['year']})"
            movie_slug = movie["slug"]
        else:
            movie = pick_movie_from_queue(history)
            movie_slug = movie["slug"]
            topic = movie.get("topic") or f"{movie['title']} ({movie['year']})"

        print(f"[Movie] {topic} (slug={movie_slug})", flush=True)
        srt_text = fetch_srt_text(movie_slug, pipeline)
        blocks = parse_srt(srt_text)
        if len(blocks) < 20:
            sys.exit(f"SRT too short ({len(blocks)} lines) for {movie_slug}")

        (out / "subtitles.srt").write_text(srt_text, encoding="utf-8")

        created = notebooklm_json_with_retry(
            "create", f"{niche.get('name', 'Retro Movie Archive')} {run_id}", "--use"
        )
        notebook_id = extract_notebook_id(created)

        srt_tmp = out / "_srt_upload.txt"
        srt_tmp.write_text(srt_text, encoding="utf-8")
        print("  Adding SRT as NotebookLM source...", flush=True)
        added = notebooklm_json_with_retry(
            "source", "add", str(srt_tmp.resolve()),
            "--notebook", notebook_id,
            "--request-timeout", str(pipeline.get("source_request_timeout", 180)),
        )
        srt_tmp.unlink(missing_ok=True)
        wait_sources(notebook_id, [extract_source_id(added)], timeout=int(pipeline.get("source_wait_timeout", 900)))

        style_notes = _ingest_style_sources(notebook_id, pipeline)
        (out / "style_notes.txt").write_text(style_notes, encoding="utf-8")

        movie_title = topic.split("—")[0].strip() if "—" in topic else topic
        print("[Script] Multi-part recap...", flush=True)
        story_prompt = (
            load_prompt("story_generation.txt")
            .replace("{movie_title}", movie_title)
            .replace("{duration_minutes}", str(duration))
            .replace("{continue_keyword}", continue_word)
            .replace("{target_words}", str(target_words))
            .replace("{style_notes}", style_notes)
        )
        script, story_parts = collect_multipart_text(notebook_id, story_prompt, continue_word, new=True)
        script = clean_script_for_tts(script)
        word_count = len(script.split())
        print(f"  -> {word_count} words (target ~{target_words})", flush=True)

        scene_count = estimate_scene_count(script, pipeline)
        segments = split_script_for_scenes(script, scene_count)
        print(f"  -> {scene_count} narration scenes", flush=True)

        print("[Scene map] Subtitle line ranges...", flush=True)
        map_prompt = build_scene_mapping_prompt(segments, blocks, pipeline)
        map_raw = ask(notebook_id, map_prompt, new=True, request_timeout=300)
        (out / "scene_mapping_raw.txt").write_text(map_raw, encoding="utf-8")
        try:
            mapping = parse_scene_mapping(map_raw, scene_count)
        except ValueError:
            print("  Retrying scene map with stricter JSON prompt...", flush=True)
            retry = map_prompt + "\n\nReply with ONLY raw JSON. No markdown."
            map_raw = ask(notebook_id, retry, new=True, request_timeout=300)
            mapping = parse_scene_mapping(map_raw, scene_count)

        scene_clips = resolve_scene_clips(mapping, segments, blocks, pipeline)
        print(f"  -> {len(scene_clips)} clips resolved from SRT", flush=True)

        past_topics = format_topic_history_for_prompt(history)
        print("[SEO] YouTube metadata...", flush=True)
        seo_prompt = (
            load_prompt("youtube_seo.txt")
            .replace("{topic}", topic)
            .replace("{past_topics}", past_topics)
            .replace("{style_notes}", style_notes)
        )
        seo_raw = ask(notebook_id, seo_prompt, new=True)
        try:
            seo = parse_seo_json(seo_raw)
        except ValueError:
            seo = fallback_seo(topic)

        thumbnail_meta = None
        if pipeline.get("generate_thumbnail", True):
            from thumbnail_builder import parse_thumbnail_json

            thumb_prompt = (
                load_prompt("thumbnail.txt")
                .replace("{topic}", topic)
                .replace("{title}", seo.get("title", topic))
                .replace("{style_notes}", style_notes)
            )
            thumb_raw = ask(notebook_id, thumb_prompt, new=True)
            (out / "thumbnail_raw.txt").write_text(thumb_raw, encoding="utf-8")
            try:
                thumb_spec = parse_thumbnail_json(thumb_raw)
            except ValueError:
                thumb_spec = {
                    "image_search_query": topic,
                    "overlay_title": movie_title.split("(")[0].strip()[:20],
                    "overlay_subtitle": "RECAP",
                }
            thumbnail_meta = {
                **thumb_spec,
                "topic": topic,
                "title": seo.get("title", topic),
            }
            print(f"  -> thumbnail spec: {thumbnail_meta.get('overlay_title')}", flush=True)

    scenes = clips_to_scenes(scene_clips)
    (out / "script.txt").write_text(script, encoding="utf-8")
    (out / "topics.txt").write_text(topic, encoding="utf-8")
    save_json(out / "scene_clips.json", {"movie_slug": movie_slug, "scenes": scene_clips})
    save_json(out / "scenes.json", scenes)
    save_json(
        out / "script_segments.json",
        [{"scene_id": c["scene_id"], "text": c.get("narration", "")} for c in scene_clips],
    )
    save_json(out / "youtube_seo.json", seo)
    if thumbnail_meta:
        save_json(out / "thumbnail.json", thumbnail_meta)

    if not args.dry_run:
        append_topic_history(
            CONFIG / "topic_history.json",
            run_id=run_id,
            topic=topic,
            title=str(seo.get("title", topic)),
        )

    meta: dict[str, Any] = {
        "run_id": run_id,
        "notebook_id": notebook_id,
        "niche": niche.get("name"),
        "movie_slug": movie_slug,
        "topic": topic,
        "duration_minutes": duration,
        "word_count": len(script.split()),
        "target_word_count": target_words,
        "scene_count": len(scene_clips),
        "title": seo.get("title"),
    }
    if not args.dry_run:
        meta["story_parts"] = story_parts
    save_json(out / "metadata.json", meta)

    print(f"run_id={run_id}")
    print(f"movie_slug={movie_slug}")
    print(f"Done: script + {len(scene_clips)} scene clips + SEO -> {out}")


if __name__ == "__main__":
    main()
