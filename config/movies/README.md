# Local movie library (dev only)

Place films here for local Phase 1 testing without VPS:

```
config/movies/{slug}/
  movie.mp4        # optional locally — VPS has the real file
  subtitles.srt    # required for phase1
```

Match `{slug}` to `config/movie_queue.json`.
