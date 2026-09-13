"""Stock-video backgrounds from Pexels (free API, free commercial use).

Flow: Gemini picks 2 short English search keywords per scene from the script
-> we search Pexels videos (portrait HD first, any orientation as backup),
avoiding previously used clips (state/used_pexels.json) -> download the best
match -> trim/scale with ffmpeg.

Cascade: Pexels -> Nano Banana images -> editorial poster. A video never fails
because of one provider.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

from .config import REPO_ROOT, env, load_json, save_json

API_URL = "https://api.pexels.com/videos/search"
USED_FILE = REPO_ROOT / "state" / "used_pexels.json"

FALLBACK_KEYWORDS = [
    "ancient architecture",
    "old books",
    "desert landscape",
    "night sky stars",
    "old city streets",
    "candles dark",
]


class PexelsError(RuntimeError):
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
    """Ask Gemini for 2 concrete ENGLISH stock-search keywords for a scene."""
    lines = script.narration
    per = max(1, (len(lines) + n_scenes - 1) // n_scenes)
    excerpt = " ".join(lines[scene_idx * per : (scene_idx + 1) * per])[:400]
    i = (scene_idx * 2) % len(FALLBACK_KEYWORDS)
    fallback = [FALLBACK_KEYWORDS[i], FALLBACK_KEYWORDS[(i + 1) % len(FALLBACK_KEYWORDS)]]
    if gem is None:
        return fallback
    try:
        data = gem.json_text(
            "You pick stock-footage search keywords. The scene narrates: "
            f"\"{excerpt}\"\n"
            "Give 2 short keywords (1-2 words each) of VISUAL things to film "
            "(places, objects, nature - no people close-ups, no text). "
            "STRICT RULES: English only, ASCII letters only, no quotes. "
            'Return STRICT JSON: {"k1": "...", "k2": "..."}',
            temperature=0.4)
        kws = [str(data.get("k1", "")).strip().strip('"'),
               str(data.get("k2", "")).strip().strip('"')]
        kws = [k for k in kws if k and len(k) < 40 and k.isascii()]
        return kws or fallback
    except Exception:  # noqa: BLE001 — keywords are best-effort
        return fallback


def _usable_files(v: dict) -> list[dict]:
    """HD mp4 variants of one video, smallest first.

    HD test: portrait clips need height >= 1280, landscape clips need
    width >= 1280 (a landscape Full-HD 1920x1080 is perfectly usable
    since ffmpeg scale-crops it to the 1080x1920 canvas anyway).
    """
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
        if v.get("id") in used or (v.get("duration") or 0) < 8:
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
        return None

    used = set(load_json(USED_FILE, {"ids": []}).get("ids", []))
    query = " ".join(keywords[:2])
    try:
        data = _search(api_key, query, "portrait")
        candidates = _collect(data, used)
        if not candidates:
            print(f"    [pexels] no portrait clip for '{query}' — retrying any orientation")
            data = _search(api_key, query, None)
            candidates = _collect(data, used)
    except Exception as exc:  # noqa: BLE001
        print(f"    [pexels] search failed ({str(exc)[:100]})")
        return None

    if not candidates:
        print(f"    [pexels] no unused clip for '{query}'")
        return None

    vid, link, kind = candidates[idx % len(candidates)]
    try:
        _download(link, out, api_key)
    except Exception as exc:  # noqa: BLE001
        print(f"    [pexels] download failed ({str(exc)[:100]})")
        return None

    used.add(vid)
    save_json(USED_FILE, {"ids": sorted(used)[-500:]})
    print(f"    [pexels] '{query}' -> clip #{vid} ({kind})")
    return out
