#!/usr/bin/env python3
"""
Phase 2 — Azure Speech TTS (primary) + edge-tts fallback

  Single narrator: ChristopherNeural (movie recap standard)
  Quote lines in double quotes → Guy/Aria via SSML voice switch
  Per-scene audio + captions.srt for YouTube upload
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import edge_tts

from azure_tts import azure_configured, build_simple_ssml, build_ssml, quota_warning, synthesize_ssml
from captions import (
    CHRISTOPHER,
    attach_punctuation_from_text,
    estimate_word_timings,
    merge_srt_blocks,
    resolve_voice,
)
from common import CONFIG, clean_script_for_tts, load_json, save_json, split_script_for_scenes
from music_cues import plan_music_cue, smooth_scene_volumes
from tts_narration import plan_outro_chunk, plan_scene_chunks, total_character_estimate

DEFAULT_VOICES = [CHRISTOPHER]
MAX_TTS_RETRIES = 4
EMPTY_SCENE_SEC = 0.35


def write_silent_mp3(dest: Path, duration: float = EMPTY_SCENE_SEC) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", str(duration), "-c:a", "libmp3lame", "-q:a", "9", str(dest),
        ],
        check=True,
        capture_output=True,
    )


async def synthesize_edge(
    text: str, voice: str, rate: str, dest: Path
) -> tuple[str, list[dict[str, Any]]]:
    if not text.strip():
        write_silent_mp3(dest, EMPTY_SCENE_SEC)
        return "", []

    voice = resolve_voice(voice)
    last_err: Exception | None = None
    for attempt in range(MAX_TTS_RETRIES):
        communicate = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
        submaker = edge_tts.SubMaker()
        words: list[dict[str, Any]] = []
        try:
            with dest.open("wb") as audio_file:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_file.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        submaker.feed(chunk)
                        start = chunk["offset"] / 10_000_000
                        duration = chunk["duration"] / 10_000_000
                        words.append({
                            "text": chunk["text"],
                            "start": round(start, 4),
                            "end": round(start + duration, 4),
                        })
            if dest.stat().st_size == 0:
                raise edge_tts.exceptions.NoAudioReceived("TTS produced empty audio file")
            return submaker.get_srt(), words
        except Exception as exc:
            dest.unlink(missing_ok=True)
            last_err = exc
            if attempt + 1 < MAX_TTS_RETRIES:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise
    raise last_err or RuntimeError("edge-tts failed")


def synthesize_azure_scene(
    chunks: list[dict[str, Any]],
    dest: Path,
    *,
    default_voice: str,
    style_degree: float,
    plain_text: str = "",
) -> None:
    ssml = build_ssml(chunks, default_voice=default_voice, style_degree=style_degree)
    if not ssml:
        write_silent_mp3(dest, EMPTY_SCENE_SEC)
        return

    last_err: Exception | None = None
    for attempt in range(MAX_TTS_RETRIES):
        try:
            synthesize_ssml(ssml, dest)
            return
        except Exception as exc:
            last_err = exc
            err = str(exc).lower()
            if "400" in err and plain_text.strip():
                try:
                    simple = build_simple_ssml(plain_text, voice=default_voice)
                    synthesize_ssml(simple, dest)
                    print("  Azure simple SSML fallback succeeded", flush=True)
                    return
                except Exception as simple_exc:
                    last_err = simple_exc
            retryable = any(s in err for s in ("429", "timeout", "503", "502", "connect"))
            if retryable and attempt + 1 < MAX_TTS_RETRIES:
                wait = 2.0 * (attempt + 1)
                print(f"  Azure TTS retry {attempt + 2}/{MAX_TTS_RETRIES} in {wait:.0f}s...", flush=True)
                time.sleep(wait)
                continue
            raise
    raise last_err or RuntimeError("Azure TTS failed")


def concat_audio(parts: list[Path], output: Path) -> None:
    if not parts:
        raise ValueError("No audio segments to concatenate")
    missing = [p for p in parts if not p.exists() or p.stat().st_size == 0]
    if missing:
        raise ValueError(f"Missing or empty audio segments: {missing}")
    list_file = output.parent / "_concat_list.txt"
    with list_file.open("w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{p.resolve().as_posix()}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(output)],
        check=True,
        capture_output=True,
    )
    list_file.unlink(missing_ok=True)


def probe_duration(path: Path) -> float:
    if not path.exists() or path.stat().st_size == 0:
        return 0.5
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return max(0.5, float(result.stdout.strip()))


def _estimate_srt_from_duration(text: str, duration: float) -> str:
    if not text.strip() or duration <= 0:
        return ""
    return f"1\n00:00:00,000 --> {_format_srt_time(duration)}\n{text.strip()}\n"


def _format_srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


async def run_phase(
    input_dir: Path,
    output_dir: Path,
    voices: list[str],
    rate: str,
    pipeline: dict[str, Any],
) -> None:
    script = clean_script_for_tts((input_dir / "script.txt").read_text(encoding="utf-8"))
    scenes_meta = load_json(input_dir / "scenes.json")
    if not script or not scenes_meta:
        raise ValueError("Need script.txt and scenes.json")

    segments_path = input_dir / "script_segments.json"
    if segments_path.exists():
        segments_data = load_json(segments_path)
        segments = [clean_script_for_tts(s.get("text", "")) for s in segments_data]
    else:
        segments = [clean_script_for_tts(t) for t in split_script_for_scenes(script, len(scenes_meta))]

    if len(segments) != len(scenes_meta):
        segments = [clean_script_for_tts(t) for t in split_script_for_scenes(script, len(scenes_meta))]

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "script_clean.txt").write_text(script, encoding="utf-8")

    captions_cfg = pipeline.get("captions", {})
    want_word_timings = bool(captions_cfg.get("generate_word_timings", False))
    style_degree = float(pipeline.get("tts_azure_style_degree", 0.92))
    default_voice = resolve_voice(voices[0] if voices else CHRISTOPHER)
    use_azure = str(pipeline.get("tts_provider", "azure")).lower() == "azure" and azure_configured()

    end_cfg = pipeline.get("end_card", {})
    end_script = ""
    if end_cfg.get("enabled", True):
        end_script = end_cfg.get(
            "script",
            "If you want more classic films broken down the same way, subscribe to Retro Movie Archive. Thanks for watching.",
        )

    char_est = total_character_estimate(segments, pipeline, end_card=end_script)
    warn = quota_warning(char_est)
    if warn:
        print(f"  WARNING: {warn}", flush=True)
    if use_azure:
        print(f"  TTS provider: azure (edge-tts fallback) (~{char_est:,} chars)", flush=True)
    else:
        print(f"  TTS provider: edge-tts (Azure not configured) (~{char_est:,} chars)", flush=True)

    narration = output_dir / "narration.mp3"
    durations: list[dict] = []
    part_files: list[Path] = []
    srt_blocks: list[str] = []
    offsets: list[float] = []
    word_timings: list[dict] = []
    clock = 0.0
    tts_backend = "azure" if use_azure else "edge"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for i, item in enumerate(scenes_meta):
            sid = int(item["scene_id"])
            text = segments[i] if i < len(segments) else ""
            part = tmp_path / f"scene_{sid:02d}.mp3"
            srt = ""
            words: list[dict[str, Any]] = []
            scene_voice = default_voice
            scene_backend = tts_backend

            if text.strip():
                if use_azure:
                    chunks = plan_scene_chunks(text, scene_index=i, pipeline=pipeline)
                    try:
                        synthesize_azure_scene(
                            chunks,
                            part,
                            default_voice=default_voice,
                            style_degree=style_degree,
                            plain_text=text,
                        )
                        scene_voice = chunks[0].get("voice", default_voice) if chunks else default_voice
                        scene_backend = "azure"
                    except Exception as exc:
                        print(f"  Azure failed scene {sid}: {exc} — edge-tts fallback", flush=True)
                        srt, words = await synthesize_edge(text, default_voice, rate, part)
                        scene_backend = "edge-fallback"
                else:
                    srt, words = await synthesize_edge(text, default_voice, rate, part)
                    scene_backend = tts_backend
            else:
                write_silent_mp3(part, EMPTY_SCENE_SEC)

            dur = probe_duration(part)
            if not srt and text.strip():
                srt = _estimate_srt_from_duration(text, dur)
            if want_word_timings and text.strip() and not words:
                words = estimate_word_timings(text, dur)
            if want_word_timings and text.strip() and words:
                words = attach_punctuation_from_text(words, text)

            durations.append({
                "scene_id": sid,
                "duration_sec": round(dur, 3),
                "file": f"scene_{sid:02d}.mp4",
                "voice": scene_voice,
                "tts_backend": scene_backend,
            })
            if want_word_timings:
                word_timings.append({"scene_id": sid, "voice": scene_voice, "words": words})
            part_files.append(part)
            srt_blocks.append(srt)
            offsets.append(clock)
            clock += dur

        concat_audio(part_files, narration)

    bg_cfg = pipeline.get("bg_music", {})
    base_vol = float(bg_cfg.get("volume", 0.12))
    clips_path = input_dir / "scene_clips.json"
    clip_moods: dict[int, str | None] = {}
    if clips_path.exists():
        clip_data = load_json(clips_path)
        for row in clip_data.get("scenes", []):
            mood = row.get("music_mood")
            if mood:
                clip_moods[int(row["scene_id"])] = str(mood).lower().split("|")[0].strip()

    music_rows: list[dict[str, Any]] = []
    for i, row in enumerate(durations):
        sid = int(row["scene_id"])
        text = segments[i] if i < len(segments) else ""
        cue = plan_music_cue(
            text,
            scene_index=i,
            total_scenes=len(durations),
            base_volume=base_vol,
            mood_override=clip_moods.get(sid),
        )
        music_rows.append(cue)

    music_rows = smooth_scene_volumes(music_rows)
    for row, cue in zip(durations, music_rows):
        row.update(cue)

    save_json(output_dir / "scene_durations.json", durations)
    if want_word_timings:
        save_json(output_dir / "word_timings.json", word_timings)
    srt_full = merge_srt_blocks(srt_blocks, offsets)
    (output_dir / "captions.srt").write_text(srt_full, encoding="utf-8")

    save_json(
        output_dir / "script_segments.json",
        [{"scene_id": int(s["scene_id"]), "text": segments[i] if i < len(segments) else ""}
         for i, s in enumerate(scenes_meta)],
    )

    if end_cfg.get("enabled", True):
        end_voice = resolve_voice(end_cfg.get("voice", default_voice))
        end_path = output_dir / "end_card.mp3"
        try:
            if azure_configured():
                outro_chunk = plan_outro_chunk(end_script, pipeline)
                synthesize_azure_scene(
                    [outro_chunk],
                    end_path,
                    default_voice=end_voice,
                    style_degree=style_degree,
                    plain_text=end_script,
                )
            else:
                await synthesize_edge(end_script, end_voice, rate, end_path)
        except Exception as exc:
            print(f"  End card Azure failed: {exc} — edge-tts", flush=True)
            await synthesize_edge(end_script, end_voice, rate, end_path)
        save_json(
            output_dir / "end_card.json",
            {
                "enabled": True,
                "image": end_cfg.get("image", "config/end_card/subscribe.png"),
                "duration_sec": round(probe_duration(end_path), 3),
                "script": end_script,
            },
        )
        print(f"Wrote end_card.mp3 ({probe_duration(end_path):.1f}s)", flush=True)

    meta = load_json(input_dir / "metadata.json") if (input_dir / "metadata.json").exists() else {}
    meta["total_audio_sec"] = round(sum(d["duration_sec"] for d in durations), 3)
    meta["tts_voices"] = voices
    meta["tts_provider"] = tts_backend
    meta["tts_char_estimate"] = char_est
    target = meta.get("duration_minutes", 0) * 60
    if target:
        meta["duration_drift_sec"] = round(meta["total_audio_sec"] - target, 1)
    save_json(output_dir / "metadata.json", meta)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2: Azure TTS + edge-tts fallback")
    parser.add_argument("--input", type=Path, default=Path("output"))
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--pipeline", type=Path, default=CONFIG / "pipeline.json")
    parser.add_argument("--rate", default=None)
    args = parser.parse_args()

    pipeline = load_json(args.pipeline) if args.pipeline.exists() else {}
    voices = pipeline.get("tts_voices") or DEFAULT_VOICES
    if isinstance(voices, str):
        voices = [voices]
    env_voice = os.environ.get("TTS_VOICE", "").strip()
    if env_voice:
        voices = [resolve_voice(env_voice)]
    else:
        voices = [resolve_voice(v) for v in voices]
    rate = args.rate or os.environ.get("TTS_RATE") or pipeline.get("tts_rate", "-4%")
    captions_cfg = pipeline.get("captions", {})
    want_word_timings = bool(captions_cfg.get("generate_word_timings", False))

    try:
        asyncio.run(run_phase(args.input, args.output, voices, rate, pipeline))
    except Exception as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    print(
        f"Wrote narration.mp3 + captions.srt"
        + (" + word_timings.json" if want_word_timings else "")
        + f" -> {args.output}"
    )


if __name__ == "__main__":
    main()
