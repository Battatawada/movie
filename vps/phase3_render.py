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


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f"Command failed: {' '.join(cmd)}")


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

    if source_dur >= output_duration:
        _run([
            "ffmpeg", "-y",
            "-ss", start, "-to", end,
            "-i", str(movie),
            "-an", "-vf", vf,
            "-t", str(output_duration),
            "-pix_fmt", "yuv420p", str(dest),
        ])
        return

    # Source shorter than narration — loop then trim
    _run([
        "ffmpeg", "-y",
        "-ss", start, "-to", end,
        "-stream_loop", "-1",
        "-i", str(movie),
        "-an", "-vf", vf,
        "-t", str(output_duration),
        "-pix_fmt", "yuv420p", str(dest),
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


def _mix_bg_music(voice_mp3: Path, inputs: Path, work: Path, duration: float) -> Path:
    import os

    cfg = _load_bg_music_config(inputs)
    track = _resolve_bg_track(cfg, inputs)
    if not track:
        return voice_mp3

    volume = float(cfg.get("volume", 0.12))
    fade_in = float(cfg.get("fade_in_sec", 2.0))
    fade_out = float(cfg.get("fade_out_sec", 3.0))
    out = work / "mixed_audio.mp3"
    fade_out_start = max(0.0, duration - fade_out)
    _run([
        "ffmpeg", "-y",
        "-i", str(voice_mp3),
        "-stream_loop", "-1", "-i", str(track),
        "-filter_complex",
        (
            f"[1:a]volume={volume},afade=t=in:st=0:d={fade_in},"
            f"afade=t=out:st={fade_out_start}:d={fade_out}[bg];"
            f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        ),
        "-map", "[aout]", "-t", str(duration),
        "-c:a", "libmp3lame", "-q:a", "4", str(out),
    ])
    return out


async def run_render_async(
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
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(video_only)])

    narration = inputs / "narration.mp3"
    end_audio = inputs / "end_card.mp3"
    audio_paths = [narration]
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
                _run([
                    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(work / "concat_end.txt"), "-c", "copy", str(combined),
                ])
                video_only = combined
                audio_paths.append(end_audio)

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
    audio_in = str(_mix_bg_music(voice_audio, inputs, work, video_dur))

    state["phase"] = "mux"
    _write_state(state_path, state)

    final = out_dir / "final_video.mp4"
    _run([
        "ffmpeg", "-y", "-i", str(video_only), "-i", audio_in,
        "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p",
        "-shortest", str(final),
    ])

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
