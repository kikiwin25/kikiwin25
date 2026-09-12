"""Pre-flight check:  python -m shorts.doctor"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .config import REPO_ROOT, load_config


def main() -> int:
    cfg = load_config()
    results: list[tuple[bool, str]] = []

    def check(ok: bool, label: str, extra: str = "") -> None:
        results.append((ok, label + (f" — {extra}" if extra else "")))

    # python
    check(sys.version_info >= (3, 10), "Python >= 3.10", sys.version.split()[0])

    # ffmpeg
    from .assemble import find_ffmpeg

    try:
        check(True, "ffmpeg", Path(find_ffmpeg()).name)
    except SystemExit as exc:
        check(False, "ffmpeg", str(exc).splitlines()[0])

    # libass support (karaoke captions)
    try:
        import subprocess

        ff = find_ffmpeg()
        out = subprocess.run([ff, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        check("ass " in out or "ass " in out.lower(), "ffmpeg libass (captions)")
    except Exception:  # noqa: BLE001
        check(False, "ffmpeg libass (captions)")

    # fonts (system DejaVu OR bundled repo font for Arabic etc.)
    font_ok, font_note = False, ""
    for candidate in [
        REPO_ROOT / "fonts" / "Almarai-Bold.ttf",
        REPO_ROOT / "fonts" / "Tajawal-Bold.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
        Path("/Library/Fonts/DejaVuSans-Bold.ttf"),
    ]:
        if candidate.exists():
            font_ok, font_note = True, str(candidate)
            break
    check(font_ok, "caption font", font_note or "no known font found")

    # deps
    for mod in ("google.genai", "googleapiclient", "google_auth_oauthlib", "PIL", "yaml", "dotenv"):
        try:
            __import__(mod)
            check(True, f"package {mod.split('.')[0]}")
        except ImportError:
            check(False, f"package {mod.split('.')[0]}", "pip install -r requirements.txt")

    # config
    check(bool(cfg["channel"].get("niche")), "config: channel.niche is set", cfg["channel"].get("niche", "")[:60])

    # secrets
    gem = bool(os.environ.get("GEMINI_API_KEY"))
    check(gem, "GEMINI_API_KEY", "set" if gem else "MISSING (aistudio.google.com/apikey)")
    yt_ok = all(os.environ.get(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))
    check(yt_ok, "YouTube OAuth secrets (upload)", "set" if yt_ok else "MISSING — run python -m shorts.auth_setup")

    # optional music
    music = cfg["music"]
    if music.get("enabled"):
        p = REPO_ROOT / music["file"]
        check(p.exists(), "music file", str(p) if p.exists() else f"missing: {p}")

    print("\nDoctor — pre-flight check")
    print("-" * 60)
    hard_fail = False
    for ok, label in results:
        icon = "✅" if ok else ("⚠️ " if "OAuth secrets" in label else "❌")
        if not ok and not label.startswith(("GEMINI", "YouTube OAuth")):
            hard_fail = True
        print(f" {icon} {label}")
    print("-" * 60)
    print("Pipeline is ready to run!" if not hard_fail else "Fix the ❌ items above, then re-run.")
    print("Render-only sanity check (no keys needed):  python -m shorts.run --selftest")
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
