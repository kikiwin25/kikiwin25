"""Real stock visuals from Pexels (free API, free commercial use).

Priority per scene:
  1. Pexels VIDEO clip (portrait HD first, any orientation as backup)
  2. Pexels PHOTO (real photography — ffmpeg adds the Ken Burns zoom)
  3. (handled in visuals.py) Pollinations AI image -> editorial poster

Keywords are picked LOCALLY (rotating niche list, zero Gemini calls).
Every attempt updates state/pexels_status.json with a short diagnostic
(key length, last state) so the result can be read directly from the repo.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .config import REPO_ROOT, env, load_json, save_json

API_URL = "https://api.pexels.com/videos/search"
PHOTO_URL = "https://api.pexels.com/v1/search"
USED_FILE = REPO_ROOT / "state" / "used_pexels.json"
STATUS_FILE = REPO_ROOT / "state" / "pexels_status.json"

FALLBACK_KEYWORDS = [
    "ancient architecture",
    "old books",
    "desert landscape",
    "night sky stars",
    "old city streets",
    "candles dark",
    "mosque silhouette",
    "sand dunes",
    "old map",
    "river sunset",
    "incense smoke",
    "old wooden door",
]


class PexelsError(RuntimeError):
    pass


def _status(state: str, detail: str = "") -> None:
    """Write a small diagnostic file into state/ (committed by the workflow)."""
    try:
        key = env("PEXELS_API_KEY", required=False)
        data = load_json(STATUS_FILE, {})
        data.update({
            "last_run": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            "key_len": len(key or ""),
            "state": state,
        })
        if detail:
            data["detail"] = detail[:140]
        else:
            data.pop("detail", None)
        save_json(STATUS_FILE, data)
    except Exception:  # noqa: BLE001 — diagnostics must never crash the bot
        pass


def _get(url: str, params: dict, api_key: str) -> dict:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, headers={"Authorization": api_key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download(url: str, dest: Path, api_key: str) -> None:
    req = urllib.request.Request(url, headers={"Authorization": api_key})
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(dest)


def keywords_for_scene(gem, script, scene_idx: int, n_scenes: int) -> list[str]:
    """Local stock-search keywords — ZERO Gemini calls. Rotates a hand-picked
    list that fits the channel niche; the offset comes from the script text so
    every video gets a different mix. `gem` kept for call-compat."""
    digest = hashlib.sha1(" ".join(script.narration).encode()).hexdigest()
    off = int(digest[:4], 16)
    i = (off + scene_idx * 2) % len(FALLBACK_KEYWORDS)
    return [FALLBACK_KEYWORDS[i], FALLBACK_KEYWORDS[(i + 1) % len(FALLBACK_KEYWORDS)]]


# ------------------------------------------------------------------ videos
def _usable_files(v: dict) -> list[dict]:
    """HD mp4 variants of one video, smallest first."""
    files = [f for f in v.get("video_files", [])
             if f.get("file_type") == "video/mp4"
             and ((f.get("height") or 0) >= 1280
                  or (f.get("width") or 0) >= 1280)]
    files.sort(key=lambda f: f.get("height") or 0)
    return files


def _search(api_key: str, query: str, orientation: str | None) -> dict:
    params: dict = {"query": query, "per_page": 15, "page": 1}
    if orientation:
        params["orientation"] = orientation
    return _get(API_URL, params, api_key)


def _collect(data: dict, used: set) -> list[tuple]:
    """Candidates as (portrait_first, height, id, link), best order first."""
    cands = []
    for v in data.get("videos", []):
        if f"v{v.get('id')}" in used or (v.get("duration") or 0) < 8:
            continue
        files = _usable_files(v)
        if not files:
            continue
        f = files[0]
        portrait = (f.get("width") or 0) < (f.get("height") or 0)
        cands.append((0 if portrait else 1, f.get("height") or 0, v["id"], f.get("link")))
    cands.sort()
    return [(c[2], c[3], "portrait" if c[0] == 0 else "landscape") for c in cands]


def fetch_pexels_video(keywords: list[str], idx: int, out: Path, cfg: dict) -> Path | None:
    """Search + download one HD clip. Portrait preferred, landscape fallback."""
    api_key = env("PEXELS_API_KEY", required=False)
    if not api_key:
        _status("empty_key")
        return None

    used = {str(x) for x in load_json(USED_FILE, {"ids": []}).get("ids", [])}
    query = " ".join(keywords[:2])
    try:
        data = _search(api_key, query, "portrait")
        candidates = _collect(data, used)
        if not candidates:
            data = _search(api_key, query, None)
            candidates = _collect(data, used)
    except Exception as exc:  # noqa: BLE001
        print(f"    [pexels] video search failed ({str(exc)[:100]})")
        _status("video_search_failed", str(exc))
        return None

    if not candidates:
        print(f"    [pexels] no video clip for '{query}' — will try photos")
        return None

    vid, link, kind = candidates[idx % len(candidates)]
    try:
        _download(link, out, api_key)
    except Exception as exc:  # noqa: BLE001
        print(f"    [pexels] video download failed ({str(exc)[:100]})")
        _status("video_download_failed", str(exc))
        return None

    used.add(f"v{vid}")
    save_json(USED_FILE, {"ids": sorted(used)[-800:]})
    _status("ok_video")
    print(f"    [pexels] video '{query}' -> clip #{vid} ({kind})")
    return out


# ------------------------------------------------------------------ photos
def fetch_pexels_photo(keywords: list[str], idx: int, out: Path, cfg: dict) -> Path | None:
    """Download one real photo (JPG) to `out`. Caller converts to PNG."""
    api_key = env("PEXELS_API_KEY", required=False)
    if not api_key:
        _status("empty_key")
        return None

    used = {str(x) for x in load_json(USED_FILE, {"ids": []}).get("ids", [])}
    query = " ".join(keywords[:2])
    try:
        data = _get(PHOTO_URL, {
            "query": query,
            "orientation": "portrait",
            "per_page": 15,
            "page": 1,
        }, api_key)
    except Exception as exc:  # noqa: BLE001
        print(f"    [pexels] photo search failed ({str(exc)[:100]})")
        _status("photo_search_failed", str(exc))
        return None

    photos = []
    for p in data.get("photos", []):
        pid = p.get("id")
        if not pid or f"p{pid}" in used:
            continue
        src = p.get("src") or {}
        link = src.get("large2x") or src.get("large") or src.get("portrait")
        if link:
            photos.append((pid, link))
    if not photos:
        print(f"    [pexels] no photo for '{query}'")
        _status("no_photo_results")
        return None

    pid, link = photos[idx % len(photos)]
    try:
        _download(link, out, api_key)
    except Exception as exc:  # noqa: BLE001
        print(f"    [pexels] photo download failed ({str(exc)[:100]})")
        _status("photo_download_failed", str(exc))
        return None

    used.add(f"p{pid}")
    save_json(USED_FILE, {"ids": sorted(used)[-800:]})
    _status("ok_photo")
    print(f"    [pexels] photo '{query}' -> #{pid}")
    return out
