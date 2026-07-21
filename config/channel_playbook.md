# Retro Movie Archive — Channel Playbook

> Feed this document to NotebookLM before Phase 1 runs (style sources + SRT).
> Reference channels: `config/seed_channels.json`

## Channel identity

| Field | Value |
|-------|--------|
| Channel | Retro Movie Archive (`@retromoviearchive`) |
| Host voice | Single male narrator — calm, confident, spoiler-forward |
| Tone | Warm documentary recap — not horror-bait, not bro-YouTube |
| Format | 12–15 min full recap, real muted film clips + original VO + subtle ambient bed |
| Visual | Wikimedia still thumbnails + PIL text overlay (no AI scene images) |

---

## A. Title patterns (from Movie Recaps, Mystery Recapped, Haunting Tube)

**What wins clicks in this niche (different from true-crime paradox titles):**

- **Movie name + year + format keyword** for search: `Fight Club (1999) | Movie Recap`
- **Twist/ending hook** without lying: `The Ending of Shutter Island Finally Makes Sense`
- **Stakes fragment** before title: `He Was Never Real | The Sixth Sense Recap`
- **Time promise** for browse: `Inception Explained in 12 Minutes`

**Clickable shapes:**

- `[Stakes line] | [Film] Recap`
- `[Film] ([Year]) | Ending Explained`
- `[Film] — Every Twist & The Real Ending`

**Avoid:**

- ALL CAPS spam, emoji in title, `[4K]`, fake "LEAKED"
- Generic-only titles with zero hook: `Movie Recap #47`
- Duplicate film on channel page — **one film = one video, ever**

**Note:** "Recap" and "Explained" are **required SEO anchors** in this niche — unlike crime channels, do not ban them.

---

## B. Thumbnail patterns

- **One hero subject** — lead actor face or iconic still, 40%+ of frame
- **Right-weighted subject**; darker/simpler **left 40%** for overlay text
- **2–4 word overlay** (composited in post): `THE TWIST`, `ENDING EXPLAINED`, film shorthand
- **Subtitle chip:** `RECAP` or `EXPLAINED` in gold/red accent
- Film-grain, motivated light, high contrast — readable at phone size
- **Never:** 6-panel collage, studio logos, rating badges, text baked into source image

---

## C. Hook / retention (first 30–60 seconds)

Reference channels often **name the film early** (search intent) but **open mid-scene**, not with channel intro.

**Retro Movie Archive cold-open formula:**

1. **0–5 sec:** Pattern interrupt — mid-crisis line from the film's story (in medias res)
2. **5–15 sec:** Specific proof — date, object, character name, or quote fragment
3. **15–35 sec:** Film + year named once; BUT/THEREFORE open loop on the central mystery
4. **35–60 sec:** Second loop — deeper wrong turn; promise the ending will land

**New channel rule:** Hook must work in **first 30 seconds** without subscriber trust.

**Do NOT open with:** "Today we explore…", birth-date biography, 60-second plot summary before tension.

**Retention cadence (full video):**

- Micro-curiosity every 10–15 sec (faster than long-form crime)
- Pattern interrupt every 2–3 min (new location, reveal, time jump)
- Act break ~7 min: one-sentence stakes reset, no recap dump
- Ending explained clearly in final 20%

---

## D. What NOT to copy

- Horror channels' jump-scare editing when covering non-horror films
- 40–60 min "everything explained" marathons (our slot is 12–15 min)
- Profanity-bait or gore-bait thumbnails
- Dual-narrator voice switching (Mind In Minutes pattern — wrong brand for recap)
- Robotic monotone TTS with no prosody variation

---

## E. Audio stack (research-backed — human-first)

| Layer | Choice | Why |
|-------|--------|-----|
| **Primary TTS** | Azure `en-US-ChristopherNeural` | Matches top recap channels: male, mid-deep, authoritative, conversational |
| **Azure style** | `newscast-casual` @ **0.92** style degree | Sounds like a person explaining a film — not robotic documentary |
| **Humanize** | Variable pauses (200–520ms), pitch micro-drift, contraction-friendly scripts | Avoids "same cadence every sentence" AI tell |
| **Rate** | `-4%` | Slightly slower = clearer + more natural at 145 wpm |
| **Quote VO** | `en-US-GuyNeural` (male), `en-US-AriaNeural` (female) | Brief dialogue in double quotes only |
| **Fallback** | None — Azure only; pipeline fails if keys missing or quota exceeded |
| **Bg music** | `ambient_cinematic.mp3` — **per-scene volume** + 0.5s crossfades | Rises on tense/action/reveal beats, dips on hook + calm exposition |
| **Ducking** | `duck_under_voice: true` @ 65% bed level | Voice always wins — music supports, never masks |
| **End card** | Same Christopher voice, friendly outro style | 5–8 sec subscribe CTA |

**Quota math (shared Azure account, 500k chars/month):**

- ~9,500–11,000 chars per 12–15 min recap (script + end card)
- **~45–52 recaps/month** total across all channels if equal split
- Retro videos are **shorter than 25-min crime docs** → favorable quota vs sibling channels
- Save quota: `tts_merge_chunks: true`, don't re-run Phase 2, tight scripts (no fluff)

**What we deliberately exclude:**

- Emily/Andrew scene alternation (crime/psychology channel pattern)
- Irish `en-IE-EmilyNeural` as primary (wrong gender/brand for this channel)
- Karaoke burned-in captions (YouTube `.srt` upload only)
- Voice cloning or celebrity mimicry

---

## F. Metrics targets (honest — exclude owner self-watches)

| Signal | Healthy (new recap channel) | Fix if bad |
|--------|----------------------------|------------|
| CTR | 4–7%+ movie recap niche | Title + thumbnail alignment |
| Retention @ 30 sec | 35%+ | Cold open / hook package |
| Retention @ 3 min | 20%+ | First-act pacing, name characters fast |
| Avg view duration | 6–9 min on 12 min video | Micro-curiosity every 10–15 sec |

---

## G. Pipeline order (hook-first)

1. Topic pick (movie queue + `topic_history.json` dedup)
2. SRT ingest + style brief (seed channels)
3. **Hook package** → locked title, cold open, thumbnail text
4. Full script (cold open verbatim, then continue)
5. Scene mapping → SRT timestamps
6. SEO (title **locked** from hook)
7. Thumbnail spec (Wikimedia query + overlay from hook)
8. Phase 2: Azure TTS → Phase 3: VPS clip render → compose thumbnail → upload

---

*Last updated: 2026-07-22 — derived from seed_channels.json references + movie-recap niche norms.*
