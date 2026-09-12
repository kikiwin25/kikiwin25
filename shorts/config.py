"""Config loading: config.yaml + .env, deep-merged over safe defaults."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict = {
    "channel": {"niche": "", "language": "en", "language_name": ""},
    "video": {
        "target_seconds": 40,
        "scenes": 3,
        "scene_style": "cinematic, dramatic lighting, vibrant colors, photorealistic",
        "max_words_per_caption": 3,
        "uppercase_captions": True,
        "resolution": [1080, 1920],
        "fps": 30,
    },
    "tts": {"voice": "Kore", "style": "an energetic, confident narrator", "gap_seconds": 0.18},
    "captions": {
        "font": "DejaVu Sans",
        "font_size": 84,
        "margin_v": 420,
        "spoken_color": "FFEB3B",
        "unspoken_color": "FFFFFF",
        "outline_color": "101010",
    },
    "upload": {
        "privacy": "public",
        "schedule_offset_minutes": 15,
        "category_id": "24",
        "made_for_kids": False,
        "playlist_id": "",
        "append_shorts_hashtag": False,
    },
    "music": {"enabled": False, "file": "assets/music.mp3", "volume": 0.08},
    "limits": {
        "gemini_text_model": "gemini-2.5-flash",
        "gemini_image_model": "gemini-2.5-flash-image",
        "gemini_tts_model": "gemini-2.5-flash-preview-tts",
        "max_images": 3,
        "max_sentences": 14,
    },
}

LANGUAGE_NAMES = {
    "en": "English", "ar": "Arabic", "fr": "French", "es": "Spanish",
    "de": "German", "pt": "Portuguese", "it": "Italian", "tr": "Turkish",
    "hi": "Hindi", "id": "Indonesian", "ja": "Japanese", "ko": "Korean",
    "nl": "Dutch", "ru": "Russian", "zh": "Chinese", "pl": "Polish",
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: Path | None = None) -> dict:
    """Load config.yaml and merge over DEFAULTS. Also loads .env if present."""
    try:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env")
    except ImportError:
        pass

    cfg_path = Path(path) if path else REPO_ROOT / "config.yaml"
    user: dict = {}
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}

    cfg = _deep_merge(DEFAULTS, user)
    lang = str(cfg["channel"].get("language") or "en").lower()
    name = cfg["channel"].get("language_name") or LANGUAGE_NAMES.get(lang, lang)
    cfg["channel"]["language_resolved"] = name
    return cfg


# ---------------------------------------------------------------- env secrets
def env(name: str, required: bool = False, hint: str = "") -> str:
    val = os.environ.get(name, "").strip()
    if required and not val:
        raise SystemExit(
            f"\n[✗] Missing environment variable: {name}\n"
            f"    {hint or 'See README.md — Setup section.'}\n"
            f"    Locally: put it in .env   |   In Actions: add it as a repo Secret.\n"
        )
    return val


# ---------------------------------------------------------------- state files
STATE_DIR = REPO_ROOT / "state"
CACHE_DIR = STATE_DIR / "cache"
USED_TOPICS_FILE = STATE_DIR / "used_topics.json"
PUBLISHED_FILE = STATE_DIR / "published.json"


def load_json(path: Path, default):
    try:
        import json

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path: Path, data) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_used_topics() -> list[str]:
    return list(load_json(USED_TOPICS_FILE, {"topics": []}).get("topics", []))


def remember_topic(topic: str) -> None:
    topics = load_used_topics()
    topics.append(topic)
    topics = topics[-200:]  # keep the last 200 only
    save_json(USED_TOPICS_FILE, {"topics": topics})


def record_published(entry: dict) -> None:
    data = load_json(PUBLISHED_FILE, {"videos": []})
    data.setdefault("videos", []).append(entry)
    save_json(PUBLISHED_FILE, data)
