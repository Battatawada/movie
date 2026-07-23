"""ffmpeg clip extraction + final recap render on VPS."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FPS = 30
BG_COLOR = "0x000000"
FFMPEG_THREADS = os.environ.get("FFMPEG_THREADS", "1")


def _run(cmd: list[str]) -> None:
    if cmd[:2] == ["ffmpeg", "-y"] and "-threads" not in cmd:
        cmd = ["ffmpeg", "-y", "-threads", FFMPEG_THREADS, *cmd[2:]]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f"Command failed: {' '.join(cmd)}")


def _expected_video_duration(
    dur_by_id: dict[int, float],
    *,
    end_dur: float = 0.0,
) -> float:
    return sum(dur_by_id.values()) + max(0.0, end_dur)


def _assert_duration(path: Path, expected: float, label: str, *, tolerance: float = 0.95) -> float:
    actual = _probe_duration(path)
    if expected > 0 and actual < expected * tolerance:
        raise RuntimeError(
            f"{label} duration {actual:.1f}s is much shorter than expected {expected:.1f}s"
        )
    return actual


def _concat_video_segments(list_file: Path, dest: Path) -> None:
    """Re-encode concat so MP4 segment timestamps cannot truncate the output."""
    _run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(dest),
    ])


def _probe_duration(path: Path) -> float:
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


def _sec_to_ffmpeg(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _write_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def extract_clip(
    movie: Path,
    start_sec: float,
    end_sec: float,
    dest: Path,
    *,
    output_duration: float,
    fps: int = FPS,
) -> None:
    """Extract muted subclip; extend/trim to match narration duration."""
    source_dur = max(0.5, end_sec - start_sec)
    vf = (
        f"scale=1920:1080:force_original_aspect_ratio=decrease,"
        f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color={BG_COLOR},"
        f"fps={fps}"
    )
    start = _sec_to_ffmpeg(start_sec)
    end = _sec_to_ffmpeg(end_sec)

    encode_args = [
        "-an", "-vf", vf,
        "-t", str(output_duration),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-video_track_timescale", "30000",
        str(dest),
    ]
    if source_dur >= output_duration:
        _run([
            "ffmpeg", "-y",
            "-ss", start, "-to", end,
            "-i", str(movie),
            *encode_args,
        ])
        return

    # Source shorter than narration — loop then trim
    _run([
        "ffmpeg", "-y",
        "-ss", start, "-to", end,
        "-stream_loop", "-1",
        "-i", str(movie),
        *encode_args,
    ])


def extract_thumbnail(movie: Path, at_sec: float, dest: Path) -> None:
    _run([
        "ffmpeg", "-y",
        "-ss", _sec_to_ffmpeg(at_sec),
        "-i", str(movie),
        "-frames:v", "1",
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease",
        str(dest),
    ])


def _load_bg_music_config(inputs: Path) -> dict[str, Any]:
    for name in ("pipeline.json", "bg_music.json"):
        p = inputs / name
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if "bg_music" in data:
                return data["bg_music"]
            return data
    return {}


def _resolve_bg_track(cfg: dict[str, Any], inputs: Path) -> Path | None:
    if not cfg.get("enabled", False):
        return None
    track = str(cfg.get("track", "")).strip()
    candidates = [
        inputs / "bg_music.mp3",
        inputs / Path(track).name if track else Path(),
    ]
    app_root = Path(os.environ.get("APP_ROOT", "/opt/retro-movies"))
    if track:
        candidates.append(app_root / track)
        candidates.append(Path(track))
    for c in candidates:
        if c.exists() and c.stat().st_size > 1000:
            return c
    return None


def _mix_bg_music(
    voice_mp3: Path,
    inputs: Path,
    work: Path,
    duration: float,
    scene_durations: list[dict[str, Any]] | None = None,
) -> Path:
    cfg = _load_bg_music_config(inputs)
    track = _resolve_bg_track(cfg, inputs)
    if not track:
        return voice_mp3

    base_volume = float(cfg.get("volume", 0.12))
    fade_in = float(cfg.get("fade_in_sec", 2.0))
    fade_out = float(cfg.get("fade_out_sec", 3.0))
    crossfade = float(cfg.get("scene_crossfade_sec", 0.5))
    dynamic = bool(cfg.get("scene_dynamic_volume", True))
    duck = bool(cfg.get("duck_under_voice", False))
    duck_amount = float(cfg.get("duck_amount", 0.65))
    out = work / "mixed_audio.mp3"

    if dynamic and scene_durations and len(scene_durations) > 1:
        bg_track = _build_scene_dynamic_bg(track, scene_durations, work, crossfade=crossfade)
    else:
        bg_track = _build_flat_bg(track, duration, base_volume, fade_in, fade_out, work)

    if duck:
        # Sidechain-style: voice-forward mix; bg stays under narration
        _run([
            "ffmpeg", "-y",
            "-i", str(voice_mp3),
            "-i", str(bg_track),
            "-filter_complex",
            (
                f"[1:a]volume={duck_amount}[bgduck];"
                f"[0:a][bgduck]amix=inputs=2:duration=first:weights=1 0.85:dropout_transition=2[aout]"
            ),
            "-map", "[aout]", "-t", str(duration),
            "-c:a", "libmp3lame", "-q:a", "4", str(out),
        ])
    else:
        _run([
            "ffmpeg", "-y",
            "-i", str(voice_mp3),
            "-i", str(bg_track),
            "-filter_complex",
            "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map", "[aout]", "-t", str(duration),
            "-c:a", "libmp3lame", "-q:a", "4", str(out),
        ])
    return out


def _build_flat_bg(
    track: Path, duration: float, volume: float, fade_in: float, fade_out: float, work: Path,
) -> Path:
    out = work / "bg_flat.mp3"
    fade_out_start = max(0.0, duration - fade_out)
    _run([
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(track),
        "-af",
        f"volume={volume},afade=t=in:st=0:d={fade_in},afade=t=out:st={fade_out_start}:d={fade_out}",
        "-t", str(duration),
        "-c:a", "libmp3lame", "-q:a", "4", str(out),
    ])
    return out


def _build_scene_dynamic_bg(
    track: Path,
    scene_durations: list[dict[str, Any]],
    work: Path,
    *,
    crossfade: float = 0.5,
) -> Path:
    """Build bg bed with per-scene volume + crossfades between moods."""
    segments: list[Path] = []
    for i, scene in enumerate(scene_durations):
        dur = max(0.5, float(scene.get("duration_sec", 5.0)))
        vol = float(scene.get("music_volume", 0.12))
        seg = work / f"bg_seg_{i:02d}.mp3"
        # Offset into track so loops don't sound identical every scene
        seek = (i * 17.5) % 120.0
        _run([
            "ffmpeg", "-y",
            "-ss", _sec_to_ffmpeg(seek),
            "-stream_loop", "-1", "-i", str(track),
            "-t", str(dur),
            "-af", f"volume={vol}",
            "-c:a", "libmp3lame", "-q:a", "4", str(seg),
        ])
        segments.append(seg)

    if len(segments) == 1:
        return segments[0]

    # Chain acrossfade for smooth volume/mood transitions
    out = work / "bg_dynamic.mp3"
    d = min(crossfade, 0.8)
    if len(segments) == 2:
        _run([
            "ffmpeg", "-y",
            "-i", str(segments[0]), "-i", str(segments[1]),
            "-filter_complex", f"[0][1]acrossfade=d={d}:c1=tri:c2=tri[aout]",
            "-map", "[aout]", "-c:a", "libmp3lame", "-q:a", "4", str(out),
        ])
        return out

    # Build filter graph for N segments
    inputs: list[str] = []
    for seg in segments:
        inputs.extend(["-i", str(seg)])
    n = len(segments)
    fc_parts: list[str] = [f"[0][1]acrossfade=d={d}:c1=tri:c2=tri[cf1]"]
    for j in range(2, n):
        prev = f"cf{j - 1}"
        nxt = f"cf{j}" if j < n - 1 else "aout"
        fc_parts.append(f"[{prev}][{j}]acrossfade=d={d}:c1=tri:c2=tri[{nxt}]")
    _run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(fc_parts),
        "-map", "[aout]", "-c:a", "libmp3lame", "-q:a", "4", str(out),
    ])
    return out


async def run_render_async(
    run_id: str,
    *,
    runs_dir: Path,
    movies_dir: Path,
) -> None:
    import asyncio

    await asyncio.to_thread(_run_render_sync, run_id, runs_dir=runs_dir, movies_dir=movies_dir)


def _run_render_sync(
    run_id: str,
    *,
    runs_dir: Path,
    movies_dir: Path,
) -> None:
    run_path = runs_dir / run_id
    state_path = run_path / "state.json"
    inputs = run_path / "inputs"
    work = run_path / "work"
    out_dir = run_path / "output"
    work.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "running"
    state["phase"] = "clips"
    _write_state(state_path, state)

    meta = json.loads((inputs / "metadata.json").read_text(encoding="utf-8"))
    movie_slug = meta["movie_slug"]
    movie = movies_dir / movie_slug / "movie.mp4"
    if not movie.exists():
        raise FileNotFoundError(f"Movie not found: {movie}")

    scene_clips = json.loads((inputs / "scene_clips.json").read_text(encoding="utf-8"))
    scenes = scene_clips.get("scenes", scene_clips if isinstance(scene_clips, list) else [])
    durations = json.loads((inputs / "scene_durations.json").read_text(encoding="utf-8"))
    dur_by_id = {int(d["scene_id"]): float(d["duration_sec"]) for d in durations}

    clip_paths: list[Path] = []
    total = len(scenes)
    state["total_scenes"] = total

    for i, scene in enumerate(scenes):
        sid = int(scene["scene_id"])
        state["current_scene"] = sid
        state["clips_ready"] = i
        _write_state(state_path, state)

        start = float(scene["start"])
        end = float(scene["end"])
        narr_dur = dur_by_id.get(sid, 5.0)
        clip_path = work / f"clip_{sid:02d}.mp4"
        extract_clip(movie, start, end, clip_path, output_duration=narr_dur)
        clip_paths.append(clip_path)
        state["clips_ready"] = i + 1
        state["completed"] = [int(s["scene_id"]) for s in scenes[: i + 1]]
        _write_state(state_path, state)

    state["phase"] = "concat"
    _write_state(state_path, state)

    list_file = work / "concat.txt"
    with list_file.open("w", encoding="utf-8") as f:
        for p in clip_paths:
            f.write(f"file '{p.resolve().as_posix()}'\n")

    video_only = work / "video_only.mp4"
    _concat_video_segments(list_file, video_only)
    _assert_duration(
        video_only,
        _expected_video_duration(dur_by_id),
        "clip concat",
    )

    narration = inputs / "narration.mp3"
    end_audio = inputs / "end_card.mp3"
    audio_paths = [narration]
    end_dur = 0.0
    if end_audio.exists():
        end_meta_path = inputs / "end_card.json"
        if end_meta_path.exists():
            end_meta = json.loads(end_meta_path.read_text(encoding="utf-8"))
            if end_meta.get("enabled", True):
                end_dur = float(end_meta.get("duration_sec", _probe_duration(end_audio)))
                end_clip = work / "end_card.mp4"
                _run([
                    "ffmpeg", "-y", "-f", "lavfi",
                    "-i", f"color=c={BG_COLOR}:s=1920x1080:d={end_dur}",
                    "-pix_fmt", "yuv420p", str(end_clip),
                ])
                with (work / "concat_end.txt").open("w", encoding="utf-8") as f:
                    f.write(f"file '{video_only.resolve().as_posix()}'\n")
                    f.write(f"file '{end_clip.resolve().as_posix()}'\n")
                combined = work / "video_with_end.mp4"
                _concat_video_segments(work / "concat_end.txt", combined)
                video_only = combined
                audio_paths.append(end_audio)
                _assert_duration(
                    video_only,
                    _expected_video_duration(dur_by_id, end_dur=end_dur),
                    "video with end card",
                )

    if len(audio_paths) == 1:
        voice_audio = narration
    else:
        full_audio = work / "full_narration.mp3"
        with (work / "audio_concat.txt").open("w", encoding="utf-8") as f:
            for p in audio_paths:
                f.write(f"file '{p.resolve().as_posix()}'\n")
        _run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(work / "audio_concat.txt"), "-c", "copy", str(full_audio),
        ])
        voice_audio = full_audio

    video_dur = _probe_duration(video_only)
    audio_in = str(_mix_bg_music(voice_audio, inputs, work, video_dur, scene_durations=durations))

    state["phase"] = "mux"
    _write_state(state_path, state)

    final = out_dir / "final_video.mp4"
    expected_final = _probe_duration(voice_audio)
    _run([
        "ffmpeg", "-y", "-i", str(video_only), "-i", audio_in,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
        str(final),
    ])
    _assert_duration(final, expected_final, "final mux")

    thumb_path = out_dir / "thumbnail.png"
    uploaded_thumb = inputs / "thumbnail.png"
    if uploaded_thumb.exists() and uploaded_thumb.stat().st_size > 5000:
        thumb_path.write_bytes(uploaded_thumb.read_bytes())
    else:
        hook_scene = scenes[0] if scenes else {"start": 60.0}
        extract_thumbnail(movie, float(hook_scene.get("start", 60.0)), thumb_path)

    state["status"] = "complete"
    state["phase"] = "done"
    state["clips_ready"] = total
    state["error"] = None
    _write_state(state_path, state)


def main() -> None:
    import sys

    if len(sys.argv) != 2:
        print("Usage: python -m phase3_render <run_id>", file=sys.stderr)
        raise SystemExit(2)
    run_id = sys.argv[1]
    runs_dir = Path(os.environ.get("RUNS_DIR", "./runs")).resolve()
    movies_dir = Path(os.environ.get("MOVIES_DIR", "/opt/movies")).resolve()
    state_path = runs_dir / run_id / "state.json"
    try:
        _run_render_sync(run_id, runs_dir=runs_dir, movies_dir=movies_dir)
    except Exception as exc:  # noqa: BLE001
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
        else:
            state = {"run_id": run_id}
        state["status"] = "failed"
        state["error"] = str(exc)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        _write_state(state_path, state)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
