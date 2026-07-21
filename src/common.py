"""Shared utilities for the Retro Movie Archive recap pipeline."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
PROMPTS = CONFIG / "prompts"


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8").strip()


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def strip_markdown(text: str) -> str:
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    return text.strip()


def normalize_tts_punctuation(text: str) -> str:
    """Fix spacing so TTS pauses naturally at punctuation."""
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r"([,.!?;:])(?=[A-Za-z\"'])", r"\1 ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"\s*\.\s*", ". ", text)
    text = re.sub(r"\s*\?\s*", "? ", text)
    text = re.sub(r"\s*!\s*", "! ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\.\s+\.", ".", text)
    return text.strip()


def clean_script_for_tts(text: str) -> str:
    """Remove NotebookLM junk and citation markers; keep narration-only text."""
    text = strip_markdown(text)
    # Profanity / graphic markers from true-crime prompts → spoken beep
    text = re.sub(r"\[BEEP\]", "beep", text, flags=re.IGNORECASE)
    # Inline metadata (multi-part merges, conversation IDs, etc.)
    text = re.sub(
        r"(?i)\b(?:new conversation|continuing conversation|conversation)\s*:\s*"
        r"[a-f0-9-]{8,}(?:\s*\(\s*turn\s+\d+\s*\))?",
        "",
        text,
    )
    text = re.sub(r"(?i)\banswer\s*:\s*", "", text)
    text = re.sub(r"(?i)\btotal\s+(parts|scenes)\s*:\s*\d+", "", text)
    text = re.sub(r"(?i)\bpart\s+\d+\b", "", text)
    text = re.sub(r"\b[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\b", "", text, flags=re.IGNORECASE)

    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^answer:\s*$", stripped, re.IGNORECASE):
            continue
        if re.match(r"^answer:\s*total\s+(parts|scenes)\s*:\s*\d+", stripped, re.IGNORECASE):
            continue
        if re.match(r"^total\s+(parts|scenes)\s*:\s*\d+", stripped, re.IGNORECASE):
            continue
        if re.match(r"^part\s+\d+\s*$", stripped, re.IGNORECASE):
            continue
        if re.match(r"^next\s*$", stripped, re.IGNORECASE):
            continue
        if re.match(
            r"^(?:new conversation|continuing conversation|conversation)\s*:\s*[a-f0-9-]+",
            stripped,
            re.IGNORECASE,
        ):
            continue
        kept.append(stripped)
    merged = " ".join(kept)
    merged = re.sub(r"\[\d+(?:,\s*\d+)*\]", "", merged)
    merged = re.sub(r"\s+", " ", merged)
    return normalize_tts_punctuation(merged)


def extract_json_blocks(text: str) -> list[Any]:
    """Parse one or more JSON arrays/objects from LLM output."""
    blocks: list[Any] = []
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    candidates = fenced if fenced else [text]
    for chunk in candidates:
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            blocks.append(json.loads(chunk))
        except json.JSONDecodeError:
            for pattern in (r"(\{[\s\S]*\})", r"(\[[\s\S]*\])"):
                match = re.search(pattern, chunk)
                if match:
                    try:
                        blocks.append(json.loads(match.group(1)))
                        break
                    except json.JSONDecodeError:
                        continue
    if not blocks:
        raise ValueError("No JSON found in model response")
    return blocks


def split_script_scenes(script: str) -> list[tuple[int, str]]:
    """Split script on [SCENE_NN] markers."""
    pattern = re.compile(r"\[SCENE_(\d+)\]", re.IGNORECASE)
    parts = pattern.split(script)
    if len(parts) < 3:
        raise ValueError("Script must contain [SCENE_01]..[SCENE_NN] markers")
    scenes: list[tuple[int, str]] = []
    # parts: [preamble, id1, text1, id2, text2, ...]
    i = 1
    while i + 1 < len(parts):
        scene_id = int(parts[i])
        body = strip_markdown(parts[i + 1]).strip()
        if body:
            scenes.append((scene_id, body))
        i += 2
    return scenes


def is_transient_notebooklm_error(message: str) -> bool:
    """True for network/RPC timeouts that are worth retrying on CI."""
    lower = message.lower()
    return any(
        s in lower
        for s in (
            "get_notebook",
            "network error",
            "timed out",
            "timeout",
            "transportservererror",
            "server-error retries exhausted",
            "connection reset",
            "temporarily unavailable",
            "rate limit",
            "rate_limited",
        )
    )


def is_reconcilable_notebooklm_rpc_error(message: str) -> bool:
    """NotebookLM sometimes returns rpc 3/9 while the source lands asynchronously."""
    lower = message.lower()
    return any(token in lower for token in ("rpc_code=9", "rpc_code=3", "rpc error: [9]", "rpc error: [3]"))


def run_cmd(args: list[str], *, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        env=merged,
        cwd=ROOT,
    )
    if check and result.returncode != 0:
        err_text = result.stderr or result.stdout or ""
        sys.stderr.write(err_text)
        message = f"Command failed ({result.returncode}): {' '.join(args)}"
        if "--json" in args and result.stdout.strip().startswith("{"):
            try:
                payload = json.loads(result.stdout)
                if isinstance(payload, dict) and payload.get("message"):
                    message = str(payload["message"])
            except json.JSONDecodeError:
                pass
        raise RuntimeError(message)
    return result


def notebooklm(*args: str, json_out: bool = False) -> str:
    cmd = ["notebooklm", *args]
    if json_out:
        cmd.append("--json")
    result = run_cmd(cmd)
    return result.stdout.strip()


def notebooklm_json(*args: str) -> dict[str, Any]:
    """Run notebooklm with --json and parse the response envelope."""
    data = json.loads(notebooklm(*args, json_out=True))
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data.get("message") or str(data))
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected notebooklm JSON response: {data!r}")
    return data


def extract_notebook_id(payload: dict[str, Any]) -> str:
    """notebooklm 0.7+ nests create output under ``notebook``."""
    nb = payload.get("notebook", payload)
    if isinstance(nb, dict) and nb.get("id"):
        return str(nb["id"])
    raise RuntimeError(f"Unexpected notebooklm create response: {payload}")


def extract_source_id(payload: dict[str, Any]) -> str:
    """notebooklm 0.7+ nests source add output under ``source``."""
    src = payload.get("source", payload)
    if isinstance(src, dict) and src.get("id"):
        return str(src["id"])
    if isinstance(src, dict) and src.get("source_id"):
        return str(src["source_id"])
    if payload.get("source_id"):
        return str(payload["source_id"])
    raise RuntimeError(f"Unexpected notebooklm source add response: {payload}")


def append_github_output(key: str, value: str) -> None:
    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def parse_total_parts(text: str) -> int:
    match = re.search(r"Total (?:Parts|Scenes):\s*(\d+)", text, re.IGNORECASE)
    return int(match.group(1)) if match else 1


def strip_total_parts_header(text: str) -> str:
    lines = text.strip().splitlines()
    if lines and re.match(r"Total (?:Parts|Scenes):\s*\d+", lines[0], re.IGNORECASE):
        return "\n".join(lines[1:]).strip()
    if lines and re.match(r"^Answer:\s*$", lines[0], re.IGNORECASE):
        lines = lines[1:]
    if lines and re.match(r"Total (?:Parts|Scenes):\s*\d+", lines[0], re.IGNORECASE):
        return "\n".join(lines[1:]).strip()
    return text.strip()


def notebooklm_json_with_retry(*args: str, retries: int = 4) -> dict[str, Any]:
    """notebooklm_json with retries on transient RPC/network errors."""
    last_err = ""
    for attempt in range(retries):
        try:
            return notebooklm_json(*args)
        except RuntimeError as exc:
            last_err = str(exc)
            if attempt + 1 < retries and is_transient_notebooklm_error(last_err):
                wait = 15 * (attempt + 1)
                print(f"  notebooklm retry {attempt + 2}/{retries} in {wait}s...", flush=True)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(last_err)


def notebooklm_list_sources(notebook_id: str) -> list[dict[str, Any]]:
    data = notebooklm_json("source", "list", "--notebook", notebook_id)
    sources = data.get("sources")
    return list(sources) if isinstance(sources, list) else []


def _youtube_video_id(url: str) -> str | None:
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    return match.group(1) if match else None


def _source_matches_content(source: dict[str, Any], content: str) -> bool:
    src_url = str(source.get("url") or "")
    if content.startswith(("http://", "https://")):
        if content in src_url or src_url in content:
            return True
        video_id = _youtube_video_id(content)
        return bool(video_id and video_id in src_url)
    title = str(source.get("title") or "")
    path = Path(content)
    if path.exists():
        return path.name.lower() in title.lower() or path.stem.lower() in title.lower()
    return False


def reconcile_source_add(
    notebook_id: str,
    content: str,
    *,
    before_ids: set[str],
    timeout: float = 90.0,
) -> dict[str, Any] | None:
    """Poll source list after rpc 3/9 to see whether NotebookLM accepted the add."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for src in notebooklm_list_sources(notebook_id):
            sid = str(src.get("id") or "")
            if not sid or sid in before_ids:
                continue
            if _source_matches_content(src, content):
                return {
                    "source": {
                        "id": sid,
                        "title": src.get("title"),
                        "url": src.get("url"),
                    }
                }
        time.sleep(5)
    return None


def notebooklm_source_add(
    notebook_id: str,
    content: str,
    *,
    request_timeout: int = 180,
    reconcile_timeout: float = 90.0,
) -> dict[str, Any]:
    """Add a NotebookLM source and reconcile false-negative rpc 3/9 failures."""
    before_ids = {str(src.get("id")) for src in notebooklm_list_sources(notebook_id) if src.get("id")}
    args = (
        "source",
        "add",
        content,
        "--notebook",
        notebook_id,
        "--request-timeout",
        str(request_timeout),
    )
    try:
        return notebooklm_json_with_retry(*args)
    except RuntimeError as exc:
        err = str(exc)
        if not is_reconcilable_notebooklm_rpc_error(err):
            raise
        print("  source add rpc error — checking whether source landed...", flush=True)
        reconciled = reconcile_source_add(
            notebook_id,
            content,
            before_ids=before_ids,
            timeout=reconcile_timeout,
        )
        if reconciled:
            sid = reconciled["source"]["id"]
            print(f"  source landed after rpc error ({sid[:8]}...)", flush=True)
            return reconciled
        raise


def is_transient_http_status(status_code: int) -> bool:
    return status_code in {408, 429, 500, 502, 503, 504}


def httpx_get_json_with_retry(url: str, *, headers: dict | None = None, timeout: float = 60.0, retries: int = 5):
    import httpx

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = httpx.get(url, headers=headers, timeout=timeout)
            if resp.status_code in {502, 503, 504, 429} and attempt + 1 < retries:
                time.sleep(min(60, 5 * (attempt + 1)))
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            retry = isinstance(exc, Exception) and (
                "timeout" in str(exc).lower()
                or "connect" in str(exc).lower()
                or (hasattr(exc, "response") and getattr(exc.response, "status_code", 0) in {502, 503, 504, 429})
            )
            if retry and attempt + 1 < retries:
                time.sleep(min(60, 5 * (attempt + 1)))
                continue
            raise
    raise last_err or RuntimeError(f"GET failed: {url}")


def httpx_post_json_with_retry(
    url: str,
    *,
    json_body: dict,
    headers: dict | None = None,
    timeout: float = 120.0,
    retries: int = 5,
):
    import httpx

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = httpx.post(url, json=json_body, headers=headers, timeout=timeout)
            if resp.status_code in {502, 503, 504, 429} and attempt + 1 < retries:
                time.sleep(min(60, 5 * (attempt + 1)))
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt + 1 < retries and (
                "timeout" in str(exc).lower() or "connect" in str(exc).lower()
            ):
                time.sleep(min(60, 5 * (attempt + 1)))
                continue
            raise
    raise last_err or RuntimeError(f"POST failed: {url}")


def httpx_download_with_retry(
    url: str,
    dest: Path,
    *,
    headers: dict | None = None,
    timeout: float = 120.0,
    retries: int = 5,
) -> None:
    import httpx

    for attempt in range(retries):
        try:
            resp = httpx.get(url, headers=headers, timeout=timeout)
            if resp.status_code in {502, 503, 504, 429} and attempt + 1 < retries:
                time.sleep(min(60, 5 * (attempt + 1)))
                continue
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            return
        except Exception as exc:  # noqa: BLE001
            if attempt + 1 < retries and (
                "timeout" in str(exc).lower() or "connect" in str(exc).lower()
            ):
                time.sleep(min(60, 5 * (attempt + 1)))
                continue
            raise
    raise RuntimeError(f"Download failed: {url}")


def parse_image_prompt_lines(text: str) -> list[str]:
    """Parse blank-line-separated image prompts from NotebookLM output."""
    body = strip_total_parts_header(text)
    blocks = re.split(r"\n\s*\n", body)
    prompts: list[str] = []
    for block in blocks:
        line = " ".join(ln.strip() for ln in block.splitlines() if ln.strip())
        if is_valid_image_prompt(line):
            prompts.append(line)
    return prompts


_METADATA_PROMPT_RE = re.compile(
    r"^(answer:\s*)?(total parts:\s*\d+|part\s+\d+\s*$|next\s*$)",
    re.IGNORECASE,
)
_CONVERSATION_PROMPT_RE = re.compile(
    r"^(?:new conversation|continuing conversation|conversation)\s*:\s*[a-f0-9-]+",
    re.IGNORECASE,
)


def is_valid_image_prompt(line: str, *, min_words: int = 8) -> bool:
    """Drop NotebookLM headers and other non-prompt lines."""
    cleaned = line.strip()
    if not cleaned:
        return False
    if _METADATA_PROMPT_RE.match(cleaned):
        return False
    if _CONVERSATION_PROMPT_RE.match(cleaned):
        return False
    if re.search(r"(?i)\banswer:\s*total\s+(parts|scenes)\s*:", cleaned):
        return False
    if re.search(r"(?i)\b(?:new conversation|conversation)\s*:\s*[a-f0-9-]{8,}", cleaned):
        return False
    if re.match(r"^total parts:\s*\d+", cleaned, re.IGNORECASE):
        return False
    if re.match(r"^scene\s+\d+\b", cleaned, re.IGNORECASE):
        return False
    if re.search(r"\bscene\s+\d+\b.*\blearning\b", cleaned, re.IGNORECASE):
        return False
    if len(cleaned.split()) < min_words:
        return False
    return True


def strip_prompt_labels(prompt: str) -> str:
    """Remove Flow-prone title prefixes from image prompts."""
    p = " ".join(prompt.split()).strip()
    p = re.sub(r"(?i)^scene\s+\d+\s*[:\-]?\s*", "", p)
    p = re.sub(r"(?i)\b(scene|chapter|part)\s+\d+\s*title\s*[:\-]?\s*", "", p)
    return p.strip()


def cap_scenes(prompts: list[str], max_scenes: int) -> list[str]:
    if max_scenes > 0 and len(prompts) > max_scenes:
        return prompts[:max_scenes]
    return prompts


def estimate_scene_count(script: str, pipeline: dict[str, Any] | None = None) -> int:
    """Scene count from narration length — not from LLM prompt spam."""
    pipeline = pipeline or {}
    max_scenes = int(pipeline.get("max_scenes", 60))
    min_scenes = int(pipeline.get("min_scenes", 10))
    words_per_scene = int(pipeline.get("words_per_scene", 35))
    text = clean_script_for_tts(script)
    word_count = len(text.split())
    if word_count < 1:
        return min_scenes
    n = max(min_scenes, round(word_count / max(1, words_per_scene)))
    return min(max_scenes, n)


def align_scenes_to_narration(
    script: str,
    prompts: list[str],
    pipeline: dict[str, Any] | None = None,
) -> tuple[list[str], list[str]]:
    """
    One image per narrated beat. Drop tail scenes that would be silent 0.35s flashes.
    Returns (prompts, script_segments) with equal length.
    """
    pipeline = pipeline or {}
    max_scenes = int(pipeline.get("max_scenes", 60))
    min_words = int(pipeline.get("min_words_per_scene", 12))
    text = clean_script_for_tts(script)
    target = min(len(prompts), estimate_scene_count(text, pipeline), max_scenes)
    target = max(1, target)
    prompts = prompts[:target]
    segments = split_script_for_scenes(text, len(prompts))

    while len(segments) > 1 and len(segments[-1].split()) < min_words:
        segments.pop()
        prompts.pop()

    if len(segments) != len(prompts):
        segments = split_script_for_scenes(text, len(prompts))

    return prompts, segments


# One published video = entire film/franchise closed. Aliases catch sequel/rewrite titles.
CASE_ALIAS_GROUPS: list[frozenset[str]] = [
    frozenset({"matrix", "neo", "morpheus", "zion", "reload", "revolutions"}),
    frozenset({"star wars", "skywalker", "vader", "empire strikes", "return of the jedi"}),
    frozenset({"lord of the rings", "frodo", "gollum", "mordor", "hobbit"}),
    frozenset({"harry potter", "hogwarts", "voldemort", "dumbledore"}),
    frozenset({"terminator", "skynet", "judgment day"}),
    frozenset({"alien", "ripley", "xenomorph", "prometheus"}),
    frozenset({"jurassic", "jurassic park", "dinosaurs"}),
    frozenset({"batman", "gotham", "dark knight", "joker"}),
    frozenset({"inception", "cobb", "dream", "limbo"}),
    frozenset({"interstellar", "cooper", "gargantua", "murph"}),
]

_TOPIC_STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "in", "on", "to", "for", "with", "from",
        "why", "how", "who", "what", "when", "where", "was", "were", "is", "are",
        "movie", "movies", "film", "films", "recap", "recaps", "summary", "full",
        "story", "explained", "ending", "classic", "retro", "archive", "plot",
        "review", "breakdown", "minute", "minutes", "mins", "part", "parts",
    }
)


def normalize_topic_text(text: str) -> str:
    t = (text or "").lower().replace("é", "e").replace("á", "a").replace("í", "i")
    t = t.replace("ó", "o").replace("ú", "u").replace("ñ", "n")
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def extract_case_keys(text: str) -> set[str]:
    """Canonical case keys for hard dedupe (aliases + distinctive tokens)."""
    norm = normalize_topic_text(text)
    if not norm:
        return set()
    keys: set[str] = set()
    for group in CASE_ALIAS_GROUPS:
        lowered = {alias.lower() for alias in group}
        if any(alias in norm for alias in lowered):
            # Store a stable group id (sorted join) plus short aliases for matching.
            keys.add("group:" + "|".join(sorted(lowered)))
            keys.update(lowered)
    for token in norm.split():
        if len(token) >= 5 and token not in _TOPIC_STOPWORDS:
            keys.add(token)
    # Bigrams of non-stopwords catch "scott peterson" style pairs.
    words = [w for w in norm.split() if w not in _TOPIC_STOPWORDS and len(w) >= 3]
    for i in range(len(words) - 1):
        bigram = f"{words[i]} {words[i + 1]}"
        if len(bigram) >= 8:
            keys.add(bigram)
    return keys


def case_keys_for_history_row(row: dict[str, Any]) -> set[str]:
    stored = row.get("case_keys")
    if isinstance(stored, list) and stored:
        return {str(x).lower() for x in stored}
    blob = " ".join(
        str(row.get(k, "")) for k in ("topic", "title", "blocked_entities") if row.get(k)
    )
    if isinstance(row.get("blocked_entities"), list):
        blob += " " + " ".join(str(x) for x in row["blocked_entities"])
    return extract_case_keys(blob)


def blocked_case_keys(history: list[dict[str, Any]] | None = None) -> set[str]:
    history = history if history is not None else load_topic_history()
    blocked: set[str] = set()
    for row in history:
        blocked |= case_keys_for_history_row(row)
    return blocked


def topic_overlaps_history(topic: str, history: list[dict[str, Any]] | None = None) -> str | None:
    """
    Return a short reason if topic is the same case (or too close) as a past video.
    One published video covers the whole case — no alternate angles.
    """
    from difflib import SequenceMatcher

    history = history if history is not None else load_topic_history()
    cand = extract_case_keys(topic)
    cand_norm = normalize_topic_text(topic)
    for row in history:
        past_label = row.get("title") or row.get("topic") or "prior video"
        past_blob = f"{row.get('title', '')} {row.get('topic', '')}".strip()
        past_norm = normalize_topic_text(past_blob)
        if cand_norm and past_norm:
            ratio = SequenceMatcher(None, cand_norm, past_norm).ratio()
            if ratio >= 0.72:
                return f"too similar to prior case ({past_label}) similarity={ratio:.2f}"

        past_keys = case_keys_for_history_row(row)
        overlap = {
            k for k in (cand & past_keys) if k not in _TOPIC_STOPWORDS and len(k) >= 5
        }
        if not overlap:
            continue
        # Require a strong signal: alias-group hit, multi-word key, or 2+ tokens.
        strong = any(k.startswith("group:") or " " in k for k in overlap) or len(overlap) >= 2
        if not strong:
            continue
        sample = ", ".join(sorted(overlap)[:4])
        return f"overlaps prior case ({past_label}) via [{sample}]"
    return None


def load_topic_history(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or CONFIG / "topic_history.json"
    if not path.exists():
        return []
    data = load_json(path)
    if isinstance(data, dict):
        return list(data.get("topics", []))
    if isinstance(data, list):
        return data
    return []


def format_topic_history_for_prompt(topics: list[dict[str, Any]], limit: int = 80) -> str:
    if not topics:
        return "(none yet — this is the first video)"
    lines: list[str] = [
        "HARD BAN — these films/franchises are CLOSED. One recap already covered each fully.",
        "Do NOT propose the same movie, sequel, or alternate recap title:",
    ]
    for row in topics[-limit:]:
        title = row.get("title") or row.get("topic") or "Unknown"
        topic = row.get("topic") or ""
        run_id = row.get("run_id", "")
        keys = sorted(k for k in case_keys_for_history_row(row) if not k.startswith("group:"))[:8]
        line = f"- DONE: {title}"
        if topic and topic != title:
            line += f" (research key: {topic})"
        if run_id:
            line += f" [{run_id}]"
        if keys:
            line += f" | ban tokens: {', '.join(keys)}"
        lines.append(line)
    lines.append(
        "Never rewrite a DONE film into a 'new' curiosity title. Pick a completely different movie."
    )
    return "\n".join(lines)


def append_topic_history(
    path: Path,
    *,
    run_id: str,
    topic: str,
    title: str,
    series_type: str | None = None,
    max_entries: int = 200,
) -> None:
    existing = load_topic_history(path)
    keys = sorted(extract_case_keys(f"{topic} {title}"))
    row: dict[str, Any] = {
        "run_id": run_id,
        "topic": topic,
        "title": title,
        "case_keys": keys,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if series_type:
        row["series_type"] = series_type
    # Replace same-case row if somehow duplicated; else append.
    existing = [
        r for r in existing if not topic_overlaps_history(topic, [r]) and not topic_overlaps_history(title, [r])
    ]
    existing.append(row)
    save_json(path, {"topics": existing[-max_entries:]})


def filter_topics_against_history(
    topics: list[str],
    history: list[dict[str, Any]] | None = None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Keep only topics that do not overlap past cases. Returns (kept, rejected_with_reason)."""
    history = history if history is not None else load_topic_history()
    kept: list[str] = []
    rejected: list[tuple[str, str]] = []
    for t in topics:
        reason = topic_overlaps_history(t, history)
        if reason:
            rejected.append((t, reason))
        else:
            kept.append(t)
    return kept, rejected


def parse_numbered_topics(raw: str) -> list[str]:
    out: list[str] = []
    for line in (raw or "").splitlines():
        cleaned = re.sub(r"^\d+[\).\s]+", "", line.strip()).strip('"').strip("'")
        if cleaned and len(cleaned) > 12:
            out.append(cleaned)
    return out


def next_series_type(history: list[dict[str, Any]] | None = None) -> str:
    """Alternate incident ↔ serial_killer. First video = incident."""
    history = history if history is not None else load_topic_history()
    if not history:
        return "incident"
    last = str(history[-1].get("series_type") or "incident").strip().lower()
    return "serial_killer" if last == "incident" else "incident"


def clips_to_scenes(scene_clips: list[dict[str, Any]]) -> list[dict]:
    """Minimal scenes.json compatible with phase2_audio."""
    return [
        {
            "scene_id": int(c.get("scene_id", i + 1)),
            "clip_start": c.get("start"),
            "clip_end": c.get("end"),
            "subtitle_start": c.get("subtitle_start"),
            "subtitle_end": c.get("subtitle_end"),
        }
        for i, c in enumerate(scene_clips)
    ]


def dedupe_prompts(prompts: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for p in prompts:
        key = p.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p.strip())
    return out


def split_script_for_scenes(script: str, num_scenes: int) -> list[str]:
    """
    Split narration into N sequential chunks for per-scene TTS.
    Image prompt i aligns with audio chunk i → editor-accurate timing.
    Distributes words evenly — no empty trailing scenes.
    """
    if num_scenes < 1:
        raise ValueError("num_scenes must be >= 1")
    text = re.sub(r"\s+", " ", script.strip())
    if not text:
        return [""] * num_scenes

    sentences = re.split(r"(?<=[.!?])\s+", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return [text] + [""] * (num_scenes - 1)

    # Fewer sentences than scenes: split by word budget (avoid rapid empty tail clips)
    if len(sentences) < num_scenes:
        words = text.split()
        if not words:
            return [""] * num_scenes
        words_per = len(words) / num_scenes
        chunks: list[str] = []
        for i in range(num_scenes):
            a = int(i * words_per)
            b = len(words) if i == num_scenes - 1 else int((i + 1) * words_per)
            chunks.append(" ".join(words[a:b]))
        return chunks

    if len(sentences) <= num_scenes:
        return sentences + [""] * (num_scenes - len(sentences))

    total_words = sum(len(s.split()) for s in sentences)
    words_per_chunk = total_words / num_scenes
    chunks = []
    current: list[str] = []
    word_count = 0

    for sent in sentences:
        current.append(sent)
        word_count += len(sent.split())
        if len(chunks) < num_scenes - 1 and word_count >= words_per_chunk:
            chunks.append(" ".join(current))
            current = []
            word_count = 0

    if current:
        chunks.append(" ".join(current))

    while len(chunks) < num_scenes:
        chunks.append("")
    return chunks[:num_scenes]


def parse_seo_json(text: str) -> dict:
    blocks = extract_json_blocks(text)
    for block in blocks:
        if isinstance(block, dict) and "title" in block:
            return block
    raise ValueError("No SEO JSON object in NotebookLM response")


def parse_hook_package_json(text: str) -> dict:
    blocks = extract_json_blocks(text)
    for block in blocks:
        if isinstance(block, dict) and block.get("cold_open") and block.get("title"):
            return block
    raise ValueError("No hook package JSON in NotebookLM response")


def sanitize_seo_title(title: str, max_chars: int = 65) -> str:
    cleaned = re.sub(r"\*+", "", title or "").strip(" -–—")
    return cleaned[:max_chars].strip()


def fallback_seo(topic: str) -> dict:
    """Rich SEO metadata when NotebookLM returns non-JSON."""
    niche = load_json(CONFIG / "niche.json") if (CONFIG / "niche.json").exists() else {}
    channel = niche.get("name", "Retro Movie Archive")
    tagline = niche.get("tagline", "Classic films. Condensed. Narrated.")
    bare = sanitize_seo_title(topic)
    bare = re.sub(r"(?i)\s*[—\-–]\s*(full movie recap|recap|ending explained)\s*$", "", bare).strip()
    title = sanitize_seo_title(f"{bare} — Full Movie Recap")
    if len(title) > 65:
        title = sanitize_seo_title(f"{bare} Recap")[:65]
    description = (
        f"{tagline}\n\n"
        f"The complete story of {bare} — every major beat, twist, and the ending explained "
        f"with real film footage and original narration.\n\n"
        f"This recap covers:\n"
        f"• The opening setup and main characters\n"
        f"• The central conflict and rising action\n"
        f"• The biggest twists\n"
        f"• The ending explained\n\n"
        f"Timestamps coming soon.\n\n"
        f"Film: {bare}\n\n"
        f"Subscribe to {channel} for more classic movie recaps."
    )
    return {
        "title": title,
        "description": description,
        "tags": [
            "movie recap",
            "film recap",
            "retro movie archive",
            "ending explained",
            "movie summary",
            "full movie recap",
            "classic movies",
        ],
        "hashtags": ["#movierecap", "#retromoviearchive", "#filmrecap"],
    }
