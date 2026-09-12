"""Stock-video backgrounds from Pexels (free API, free commercial use).

Flow: Gemini picks 2-3 short English search keywords per scene from the script
-> we search Pexels videos (portrait, HD), avoiding previously used clips
(state/used_pexels.json) -> download the best match.

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
    fallbacks = [["ancient architecture", "desert dunes"],
                 ["old books", "candlelight"],
                 ["stars night sky", "mosque silhouette"]]
    if gem is None:
        return fallbacks[scene_idx % len(fallbacks)]
    try:
        data = gem.json_text(
            "You pick stock-footage search keywords. The scene narrates: "
            f"\"{excerpt}\"\n"
            "Give 2 short ENGLISH keywords (1-2 words each) describing VISUAL "
            "things to film (places, objects, nature — no people close-ups, "
            'no text). Return STRICT JSON: {"k1": "...", "k2": "..."}',
            temperature=0.4)
        kws = [str(data.get("k1", "")).strip(), str(data.get("k2", "")).strip()]
        kws = [k for k in kws if k and len(k) < 40]
        return kws or fallbacks[scene_idx % len(fallbacks)]
    except Exception:  # noqa: BLE001 — keywords are best-effort
        return fallbacks[scene_idx % len(fallbacks)]


def fetch_pexels_video(keywords: list[str], idx: int, out: Path, cfg: dict) -> Path | None:
    """Search + download one portrait HD clip. Returns path or None."""
    api_key = env("PEXELS_API_KEY", required=False)
    if not api_key:
        return None

    used = set(load_json(USED_FILE, {"ids": []}).get("ids", []))
    query = " ".join(keywords[:2])
    try:
        data = _get(API_URL, {
            "query": query,
            "orientation": "portrait",
            "size": "medium",
            "per_page": 15,
            "page": 1,
        }, api_key)
    except Exception as exc:  # noqa: BLE001
        print(f"    [pexels] search failed ({str(exc)[:100]})")
        return None

    candidates = []
    for v in data.get("videos", []):
        if v.get("id") in used:
            continue
        if (v.get("duration") or 0) < 8:      # need room to trim
            continue
        files = [f for f in v.get("video_files", [])
                 if f.get("file_type") == "video/mp4"
                 and (f.get("height") or 0) >= 1280
                 and (f.get("width") or 0) < (f.get("height") or 0)]
        if files:
            files.sort(key=lambda f: f.get("height") or 0)   # smallest >=1280
            candidates.append((v["id"], files[0].get("link")))
    if not candidates:
        print(f"    [pexels] no unused portrait clip for '{query}'")
        return None

    vid, link = candidates[idx % len(candidates)]
    try:
        _download(link, out, api_key)
    except Exception as exc:  # noqa: BLE001
        print(f"    [pexels] download failed ({str(exc)[:100]})")
        return None

    used.add(vid)
    save_json(USED_FILE, {"ids": sorted(used)[-500:]})
    print(f"    [pexels] '{query}' -> clip #{vid}")
    return out
