# Retro Movie Archive — Bootstrap Plan

## Locked concept

| Item | Value |
|------|--------|
| Channel | **Retro Movie Archive** |
| Handle | `@retromoviearchive` |
| Format | Full movie recap — real muted clips + TTS narration |
| Length | ~12 min (max 15) |
| Visuals | Source film clips (ffmpeg on VPS) — **no AI images** |
| Script source | NotebookLM + SRT transcript |
| Timestamps | SRT line ranges → programmatic clip times |
| VPS port | **8766** (crime FlowKit stays on 8765) |
| Movies dir | `/opt/movies/{slug}/` |

## Done (repo bootstrap)

- [x] Fork pipeline from True Crime
- [x] SRT parser + scene_clips.json mapping
- [x] Phase 1 movie recap script
- [x] VPS clip worker (ffmpeg render)
- [x] GHA workflow (script → VPS render → upload)
- [x] Config, prompts, movie queue template

## Still open

- [x] GitHub repo [`Battatawada/movie`](https://github.com/Battatawada/movie)
- [ ] Deploy VPS worker on Oracle (`scripts/vps-setup.sh`)
- [ ] Upload first film to `/opt/movies/{slug}/`
- [ ] Set GHA secrets (`NOTEBOOKLM_AUTH_JSON`, `VPS_*`, `YOUTUBE_*`)
- [ ] Create YouTube channel + OAuth
- [ ] First dry-run on GHA with `workflow_dispatch` + `movie_slug`
- [ ] Optional: burn karaoke captions on VPS (currently narration only)

## Movie onboarding checklist

1. Rip/obtain `movie.mp4` + `subtitles.srt`
2. `scp` to VPS: `/opt/movies/{slug}/`
3. Add to `config/movie_queue.json`:
   ```json
   {
     "slug": "inception-2010",
     "title": "Inception",
     "year": 2010,
     "enabled": true,
     "topic": "Inception (2010) — Full Movie Recap"
   }
   ```
4. Run pipeline manually with `movie_slug: inception-2010`

## VPS layout

```
/opt/retro-movies/          # this repo
/opt/movies/                # film library (persistent)
  inception-2010/
    movie.mp4
    subtitles.srt
```

## Copyright note

Recaps use short muted clips + original narration. Expect Content ID on studio films. This is an operational risk, not a technical one.
