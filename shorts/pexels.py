"""Pexels stock visuals — real videos/photos only (no AI images).

Per scene: video clip -> photo -> (visuals.py) drawn poster.
Keywords are local (zero AI calls). The video search tries the scene
keywords, then 5 generic niche queries rotating daily. Diagnostics are
written to state/pexels_status.json (video and photo states kept apart).
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

# api.pexels.com 403s the default python UA — use a browser UA.
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

FALLBACK_KEYWORDS = [
    "ancient architecture", "old books", "desert landscape", "night sky stars",
    "old city streets", "candles dark", "mosque silhouette", "sand dunes",
    "old map", "river sunset", "incense smoke", "old wooden door",
]


class PexelsError(RuntimeError):
    pass


def _orientation(cfg: dict) -> str:
    return str((cfg.get("video") or {}).get("pexels_orientation") or "portrait")


def _vstatus(kind: str, state: str, detail: str = "") -> None:
    """Persistent diagnostics; keeps last_video and last_photo separate."""
    try:
        key = env("PEXELS_API_KEY", required=False)
        data = load_json(STATUS_FILE, {})
        stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
        data[f"last_{kind}"] = state
        data[f"last_{kind}_at"] = stamp
        data.update({"last_run": stamp, "key_len": len(key or ""), "state": state})
        if detail:
            data["detail"] = detail[:140]
        else:
            data.pop("detail", None)
        save_json(STATUS_FILE, data)
    except Exception:  # noqa: BLE001 — never crash the bot
        pass


def _get(url: str, params: dict, api_key: str) -> dict:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full, headers={
        "Authorization": api_key,
        "User-Agent": BROWSER_UA,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download(url: str, dest: Path) -> None:
    # No Authorization header — CDN links are public, the key stays on the API.
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
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
    """Local keywords — zero AI calls. Offset comes from the script text."""
    digest = hashlib.sha1(" ".join(script.narration).encode()).hexdigest()
    off = int(digest[:4], 16)
    i = (off + scene_idx * 2) % len(FALLBACK_KEYWORDS)
    return [FALLBACK_KEYWORDS[i], FALLBACK_KEYWORDS[(i + 1) % len(FALLBACK_KEYWORDS)]]


def _usable_files(v: dict) -> list[dict]:
    files = [f for f in v.get("video_files", [])
             if f.get("file_type") == "video/mp4"
             and ((f.get("height") or 0) >= 1280 or (f.get("width") or 0) >= 1280)]
    files.sort(key=lambda f: f.get("height") or 0)
    return files


def _search(api_key: str, query: str, orientation: str | None) -> dict:
    params: dict = {"query": query, "per_page": 15, "page": 1}
    if orientation:
        params["orientation"] = orientation
    return _get(API_URL, params, api_key)


def _collect(data: dict, used: set) -> list[tuple]:
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
    """Try up to 6 queries: scene keywords, then 5 generic ones (daily rotate)."""
    api_key = env("PEXELS_API_KEY", required=False)
    if not api_key:
        _vstatus("video", "empty_key")
        return None

    used = {str(x) for x in load_json(USED_FILE, {"ids": []}).get("ids", [])}
    orient = _orientation(cfg)

    queries = [" ".join(keywords[:2])]
    seed = int(time.strftime("%Y%m%d")) + idx
    for k in range(5):
        q = FALLBACK_KEYWORDS[(seed + k * 3) % len(FALLBACK_KEYWORDS)]
        if q not in queries:
            queries.append(q)

    candidates: list[tuple] = []
    used_query = ""
    for query in queries:
        try:
            data = _search(api_key, query, orient)
            candidates = _collect(data, used)
            if not candidates:
                data = _search(api_key, query, None)
                candidates = _collect(data, used)
        except Exception as exc:  # noqa: BLE001 — try next query
            print(f"    [pexels] video search failed for '{query}' ({str(exc)[:100]})")
            continue
        if candidates:
            used_query = query
            break

    if not candidates:
        _vstatus("video", "no_video_results")
        print("    [pexels] no video clip in 6 searches — will try photos")
        return None

    vid, link, kind = candidates[idx % len(candidates)]
    try:
        _download(link, out)
    except Exception as exc:  # noqa: BLE001
        print(f"    [pexels] video download failed ({str(exc)[:100]})")
        _vstatus("video", "video_download_failed", str(exc))
        return None

    used.add(f"v{vid}")
    save_json(USED_FILE, {"ids": sorted(used)[-800:]})
    _vstatus("video", "ok_video")
    print(f"    [pexels] video '{used_query}' -> clip #{vid} ({kind})")
    return out


def fetch_pexels_photo(keywords: list[str], idx: int, out: Path, cfg: dict) -> Path | None:
    """Download one real photo (JPG) to `out`. Caller converts to PNG."""
    api_key = env("PEXELS_API_KEY", required=False)
    if not api_key:
        _vstatus("photo", "empty_key")
        return None

    used = {str(x) for x in load_json(USED_FILE, {"ids": []}).get("ids", [])}
    query = " ".join(keywords[:2])
    try:
        data = _get(PHOTO_URL, {
            "query": query,
            "orientation": _orientation(cfg),
            "per_page": 15,
            "page": 1,
        }, api_key)
    except Exception as exc:  # noqa: BLE001
        print(f"    [pexels] photo search failed ({str(exc)[:100]})")
        _vstatus("photo", "photo_search_failed", str(exc))
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
        _vstatus("photo", "no_photo_results")
        return None

    pid, link = photos[idx % len(photos)]
    try:
        _download(link, out)
    except Exception as exc:  # noqa: BLE001
        print(f"    [pexels] photo download failed ({str(exc)[:100]})")
        _vstatus("photo", "photo_download_failed", str(exc))
        return None

    used.add(f"p{pid}")
    save_json(USED_FILE, {"ids": sorted(used)[-800:]})
    _vstatus("photo", "ok_photo")
    print(f"    [pexels] photo '{query}' -> #{pid}")
    return out
