"""VPS clip worker — FastAPI service for ffmpeg movie recap rendering."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Retro Movie Archive Clip Worker", version="0.1.0")

RUNS_DIR = Path(os.environ.get("RUNS_DIR", "./runs")).resolve()
MOVIES_DIR = Path(os.environ.get("MOVIES_DIR", "/opt/movies")).resolve()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
_VPS_ROOT = Path(__file__).resolve().parent

_jobs: dict[str, subprocess.Popen[bytes]] = {}

RUNS_DIR.mkdir(parents=True, exist_ok=True)

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$")
_RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}$")


class GeneratePayload(BaseModel):
    run_id: str


def verify_auth(request: Request) -> None:
    if not WEBHOOK_SECRET:
        raise HTTPException(500, "WEBHOOK_SECRET not configured")
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(401, "Unauthorized")


def _valid_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug):
        raise HTTPException(400, "Invalid slug")


def _valid_run_id(run_id: str) -> None:
    if not _RUN_ID_RE.match(run_id):
        raise HTTPException(400, "Invalid run_id")


def _state_path(run_id: str) -> Path:
    return RUNS_DIR / run_id / "state.json"


def _read_state(run_id: str) -> dict[str, Any]:
    path = _state_path(run_id)
    if not path.exists():
        raise HTTPException(404, "Run not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _prune_jobs() -> None:
    finished = [run_id for run_id, proc in _jobs.items() if proc.poll() is not None]
    for run_id in finished:
        _jobs.pop(run_id, None)


def _spawn_render(run_id: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-m", "phase3_render", run_id],
        cwd=_VPS_ROOT,
        start_new_session=True,
    )


def _job_running(run_id: str) -> bool:
    _prune_jobs()
    proc = _jobs.get(run_id)
    return proc is not None and proc.poll() is None


@app.get("/health")
async def health() -> dict[str, Any]:
    _prune_jobs()
    active_jobs = sum(1 for proc in _jobs.values() if proc.poll() is None)
    payload: dict[str, Any] = {
        "status": "busy" if active_jobs else "ok",
        "service": "retro-clip-worker",
        "active_jobs": active_jobs,
    }
    if active_jobs == 0:
        payload["runs_free_bytes"] = shutil.disk_usage(RUNS_DIR).free
    return payload


@app.get("/movies")
def list_movies(_: None = Depends(verify_auth)) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if MOVIES_DIR.is_dir():
        for d in sorted(MOVIES_DIR.iterdir()):
            if not d.is_dir():
                continue
            movie = d / "movie.mp4"
            srt = d / "subtitles.srt"
            if movie.exists():
                items.append({
                    "slug": d.name,
                    "has_srt": srt.exists(),
                    "movie_bytes": movie.stat().st_size,
                })
    return {"movies": items}


@app.delete("/movies/{slug}")
def delete_movie(slug: str, _: None = Depends(verify_auth)) -> dict[str, Any]:
    """Remove source film from the VPS library after a successful pipeline run."""
    _valid_slug(slug)
    movie_dir = MOVIES_DIR / slug
    if not movie_dir.exists():
        return {"slug": slug, "status": "not_found"}
    if not movie_dir.is_dir():
        raise HTTPException(400, "Not a movie directory")
    shutil.rmtree(movie_dir)
    return {"slug": slug, "status": "deleted"}


@app.get("/movies/{slug}/srt")
def get_movie_srt(slug: str, _: None = Depends(verify_auth)) -> JSONResponse:
    _valid_slug(slug)
    srt_path = MOVIES_DIR / slug / "subtitles.srt"
    if not srt_path.exists():
        raise HTTPException(404, f"No subtitles for {slug}")
    content = srt_path.read_text(encoding="utf-8", errors="replace")
    line_count = len([b for b in content.split("\n\n") if b.strip()])
    return JSONResponse({
        "slug": slug,
        "content": content,
        "line_count": line_count,
        "bytes": len(content.encode("utf-8")),
    })


@app.post("/runs/{run_id}/inputs")
async def upload_inputs(
    run_id: str,
    request: Request,
    _: None = Depends(verify_auth),
) -> dict[str, str]:
    """Multipart upload of pipeline artifacts (narration.mp3, scene_clips.json, etc.)."""
    if ".." in run_id:
        raise HTTPException(400, "Invalid run_id")
    free_bytes = shutil.disk_usage(RUNS_DIR).free
    if free_bytes < 256 * 1024 * 1024:
        raise HTTPException(
            507,
            f"Insufficient disk space in RUNS_DIR ({free_bytes // (1024 * 1024)} MB free)",
        )
    inputs_dir = RUNS_DIR / run_id / "inputs"
    try:
        inputs_dir.mkdir(parents=True, exist_ok=True)
        form = await request.form()
        saved = 0
        saved_names: list[str] = []
        for key, value in form.items():
            if hasattr(value, "read"):
                dest = inputs_dir / str(key)
                data = await value.read()
                dest.write_bytes(data)
                saved += 1
                saved_names.append(f"{key}({len(data)}B)")
    except OSError as exc:
        raise HTTPException(507, f"Failed to write inputs: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Upload failed: {exc}") from exc
    if saved < 3:
        raise HTTPException(400, f"Expected at least 3 input files, got {saved}")
    return {
        "run_id": run_id,
        "status": "inputs_saved",
        "files": str(saved),
        "saved": ", ".join(saved_names),
    }


@app.post("/generate")
async def generate(
    payload: GeneratePayload,
    _: None = Depends(verify_auth),
) -> dict[str, str]:
    """Start render job after inputs are uploaded."""
    run_id = payload.run_id
    if _job_running(run_id):
        return {"run_id": run_id, "status": "already_running"}

    inputs_dir = RUNS_DIR / run_id / "inputs"
    if not (inputs_dir / "scene_clips.json").exists():
        raise HTTPException(400, "Upload inputs first via POST /runs/{run_id}/inputs")

    state_path = _state_path(run_id)
    if state_path.exists():
        existing = json.loads(state_path.read_text(encoding="utf-8"))
        if existing.get("status") == "complete":
            return {"run_id": run_id, "status": "already_complete"}

    meta = json.loads((inputs_dir / "metadata.json").read_text(encoding="utf-8"))
    scene_clips = json.loads((inputs_dir / "scene_clips.json").read_text(encoding="utf-8"))
    scenes = scene_clips.get("scenes", [])
    initial = {
        "run_id": run_id,
        "status": "pending",
        "phase": "queued",
        "movie_slug": meta.get("movie_slug"),
        "total_scenes": len(scenes),
        "clips_ready": 0,
        "current_scene": 0,
        "completed": [],
        "error": None,
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(initial, indent=2), encoding="utf-8")

    _jobs[run_id] = _spawn_render(run_id)
    return {"run_id": run_id, "status": "accepted"}


@app.get("/runs/{run_id}/status")
def run_status(run_id: str, _: None = Depends(verify_auth)) -> dict[str, Any]:
    state = _read_state(run_id)
    # Back-compat for poll script expecting images_ready
    state["images_ready"] = state.get("clips_ready", 0)
    return state


@app.get("/runs/{run_id}/output/{filename}")
def get_output(run_id: str, filename: str, _: None = Depends(verify_auth)) -> FileResponse:
    if ".." in filename or "/" in filename:
        raise HTTPException(400, "Invalid filename")
    path = RUNS_DIR / run_id / "output" / filename
    if not path.exists():
        raise HTTPException(404, "File not found")
    media = "video/mp4" if filename.endswith(".mp4") else "image/png"
    return FileResponse(path, media_type=media)


@app.delete("/runs/{run_id}")
def delete_run(run_id: str, _: None = Depends(verify_auth)) -> dict[str, Any]:
    """Remove render inputs/output for a completed run."""
    _valid_run_id(run_id)
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        return {"run_id": run_id, "status": "not_found"}
    if not run_dir.is_dir():
        raise HTTPException(400, "Not a run directory")
    proc = _jobs.pop(run_id, None)
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            proc.terminate()
        proc.wait(timeout=10)
    shutil.rmtree(run_dir)
    return {"run_id": run_id, "status": "deleted"}


def main() -> None:
    import uvicorn

    host = os.environ.get("NICHE_HOST", "0.0.0.0")
    port = int(os.environ.get("NICHE_PORT", "8766"))
    uvicorn.run("clip_worker:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
