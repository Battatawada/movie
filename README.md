# Retro Movie Archive

Automated **movie recap** pipeline: NotebookLM script from subtitles → edge-tts narration → Oracle VPS ffmpeg clips from real film footage → YouTube upload.

## Architecture

```
GitHub Actions                         Oracle VPS (:8766)
────────────────                       ──────────────────
Phase 1  SRT → NotebookLM → script     /opt/movies/{slug}/
         + scene_clips.json              movie.mp4 + subtitles.srt
Phase 2  edge-tts → narration.mp3
         trigger VPS ─────────────────►  ffmpeg clip + mux
Poll + download ◄────────────────────  final_video.mp4
Phase 5  YouTube upload
```

**No AI-generated visuals.** Clips are cut from the source film using SRT timestamps.

## Quick start (local)

```powershell
cd "C:\Users\Pracheer\Music\Retro Movie Archive"
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
notebooklm login

# Add SRT for a test film:
# config\movies\my-film-1999\subtitles.srt

python src/phase1_script.py --dry-run --output output
```

## VPS movie library

On Oracle VPS, each film lives at:

```
/opt/movies/inception-2010/
  movie.mp4
  subtitles.srt
```

Add matching entry in `config/movie_queue.json` with `"enabled": true`.

## GitHub secrets

| Secret | Purpose |
|--------|---------|
| `NOTEBOOKLM_AUTH_JSON` | Phase 1 NotebookLM |
| `VPS_WEBHOOK_URL` | `http://<vps-ip>:8766` |
| `VPS_WEBHOOK_SECRET` | VPS auth bearer token |
| `YOUTUBE_*` | Phase 5 upload |

## VPS setup

```bash
sudo bash scripts/vps-setup.sh
# Then configure .env, systemd service, upload movies to /opt/movies/
```

See `BOOTSTRAP-PLAN.md` for full checklist.

## Pipeline phases

| Phase | Where | Output |
|-------|-------|--------|
| 1 | GHA | `scene_clips.json`, `script.txt`, SEO |
| 2 | GHA | `narration.mp3`, `scene_durations.json` |
| 3–4 | VPS | `final_video.mp4`, `thumbnail.png` |
| 5 | GHA | YouTube upload |

## Fork lineage

Based on [True Crime Documentaries](https://github.com/Battatawada/crime) / Mind In Minutes pipeline — with FlowKit replaced by ffmpeg clip worker.
