"""VPS clip worker — FastAPI service for ffmpeg movie recap rendering."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from phase3_render import run_render_async

app = FastAPI(title="Retro Movie Archive Clip Worker", version="0.1.0")

RUNS_DIR = Path(os.environ.get("RUNS_DIR", "./runs")).resolve()
MOVIES_DIR = Path(os.environ.get("MOVIES_DIR", "/opt/movies")).resolve()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

_jobs: dict[str, asyncio.Task] = {}

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


async def _run_job(run_id: str) -> None:
    state_path = _state_path(run_id)
    try:
        await run_render_async(run_id, runs_dir=RUNS_DIR, movies_dir=MOVIES_DIR)
    except Exception as exc:  # noqa: BLE001
        state = _read_state(run_id) if state_path.exists() else {"run_id": run_id}
        state["status"] = "failed"
        state["error"] = str(exc)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "retro-clip-worker", "movies_dir": str(MOVIES_DIR)}


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
    inputs_dir = RUNS_DIR / run_id / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    form = await request.form()
    saved = 0
    for key, value in form.items():
        if hasattr(value, "read"):
            dest = inputs_dir / str(key)
            data = await value.read()
            dest.write_bytes(data)
            saved += 1
    if saved < 3:
        raise HTTPException(400, f"Expected at least 3 input files, got {saved}")
    return {"run_id": run_id, "status": "inputs_saved", "files": str(saved)}


@app.post("/generate")
async def generate(
    payload: GeneratePayload,
    _: None = Depends(verify_auth),
) -> dict[str, str]:
    """Start render job after inputs are uploaded."""
    run_id = payload.run_id
    if run_id in _jobs and not _jobs[run_id].done():
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

    task = asyncio.create_task(_run_job(run_id))
    _jobs[run_id] = task
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
    task = _jobs.pop(run_id, None)
    if task and not task.done():
        task.cancel()
    shutil.rmtree(run_dir)
    return {"run_id": run_id, "status": "deleted"}


def main() -> None:
    import uvicorn

    host = os.environ.get("NICHE_HOST", "0.0.0.0")
    port = int(os.environ.get("NICHE_PORT", "8766"))
    uvicorn.run("clip_worker:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
