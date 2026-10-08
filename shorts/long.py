"""Long-form daily audio story (~15 min) — orchestrator.

    python -m shorts.long                 # story + render + upload
    python -m shorts.long --dry-run       # render only, skip the upload
    python -m shorts.long --topic "..."   # force today's story topic

Reuses the whole Shorts pipeline (tts -> visuals -> assemble -> upload);
the story comes from storygen.Story, which duck-types like scriptgen.Script.
Config: everything lives under `long:` in config.yaml. Default frame is
16:9 landscape (1920x1080) like a TV documentary; the Shorts stay 9:16.

v3: TTS is forced to Gemini (ElevenLabs free tier rejects >10k chars, which
is every long story), all prints are line-buffered (a silent crash now
shows exactly where it stopped), and narration gets one automatic retry
(synthesized batches are cached on disk, so a retry never re-pays them).
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import re
import sys
import wave
from pathlib import Path

from .assemble import render
from .captions import build_ass
from .config import (
    env,
    load_config,
    load_used_topics,
    record_published,
    remember_topic,
)
from .gemini_client import Gemini
from .storygen import make_story, save_story
from .tts import synthesize_narration
from .visuals import build_backgrounds

# A 15-minute video must not carry #Shorts — calmer story pack instead.
LONG_HASHTAG_PACK = "#قصص #قصص_حقيقية #غموض #وثائقي #حكايات #اكسبلور"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:40] or "story").rstrip("-")


def _new_workdir(out_dir: Path, topic: str) -> Path:
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    workdir = out_dir / f"long_{stamp}_{_slug(topic)}"
    n = 2
    while workdir.exists():
        workdir = out_dir / f"long_{stamp}_{_slug(topic)}-{n}"
        n += 1
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def _long_cfg(cfg: dict) -> dict:
    """Deep-copy cfg with the long-form overrides from `long:` in config.yaml."""
    long = cfg.get("long") or {}
    c = copy.deepcopy(cfg)
    c["tts"]["provider"] = "gemini"   # ElevenLabs free tier caps at 10k chars/story
    c["tts"]["chunks"] = int(long.get("tts_chunks", 4))       # Gemini TTS: 10 req/day
    c["tts"]["gap_seconds"] = float(long.get("tts_gap_seconds", 0.15))
    if long.get("voice"):
        c["tts"]["voice"] = long["voice"]
    if long.get("style"):
        c["tts"]["style"] = long["style"]
    scenes = int(long.get("scenes", 18))                      # 18 x ~50s = ~15 min
    c["video"]["scenes"] = scenes
    c.setdefault("limits", {})["max_images"] = scenes
    c["video"]["fps"] = int(long.get("fps", c["video"].get("fps", 30)))
    # 16:9 landscape like a TV documentary (Shorts keep their own 9:16)
    c["video"]["resolution"] = [int(long.get("width", 1920)), int(long.get("height", 1080))]
    c["video"]["pexels_orientation"] = str(long.get("pexels_orientation", "landscape"))
    # captions recalibrated for a 1920x1080 frame (Shorts values untouched)
    c.setdefault("captions", {})
    c["captions"]["font_size"] = int(long.get("caption_font_size", 72))
    c["captions"]["margin_v"] = int(long.get("caption_margin_v", 96))
    c["upload"]["append_shorts_hashtag"] = False              # handled below, long pack
    return c


def _record_narration(gem: Gemini, cfg: dict, lines: list[str], workdir: Path):
    """synthesize_narration with unbuffered logs and one automatic retry."""
    try:
        sys.stdout.reconfigure(line_buffering=True)   # crash logs stay visible
    except Exception:  # noqa: BLE001 — very old Pythons only
        pass

    import time
    import traceback

    for attempt in (1, 2):
        try:
            return synthesize_narration(gem, cfg, lines, workdir)
        except SystemExit:
            raise                    # quota / config advice — never retry these
        except Exception:
            print(f"  [warn] narration attempt {attempt}/2 failed:")
            traceback.print_exc()
            sys.stdout.flush()
            if attempt == 1:
                print("  [warn] retrying once — finished batches are cached, "
                      "they will NOT be synthesized again ...")
                time.sleep(5)
            else:
                raise SystemExit("[✗] Narration failed twice — see the "
                                 "traceback above for the exact cause.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shorts.long", description="Daily ~15-minute audio story")
    ap.add_argument("--topic", default="", help="force today's story topic")
    ap.add_argument("--dry-run", action="store_true", help="render only, skip the upload")
    ap.add_argument("--out-dir", default="outputs", help="where finished videos go")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    cfg = load_config()
    if not bool((cfg.get("long") or {}).get("enabled", False)):
        print("[long] disabled — set `long.enabled: true` in config.yaml.")
        print("       Verify the channel at https://www.youtube.com/verify first:")
        print("       uploads longer than 15 minutes are rejected otherwise.")
        return 1

    c = _long_cfg(cfg)

    print("=" * 62)
    print(" Long-form audio story — ~15 minutes (16:9)")
    print("=" * 62)
    api_key = env("GEMINI_API_KEY", True, "Get a free key at https://aistudio.google.com/apikey")
    gem = Gemini(api_key, cfg)

    # 1 — story -------------------------------------------------------------
    print("\n[1/6] Writing today's story (~2000 words, 11-12 chapters) ...")
    used = load_used_topics()
    story = make_story(gem, c, args.topic or None, used)
    if cfg["upload"].get("append_shorts_hashtag"):
        story.description = (story.description + "\n\n" + LONG_HASHTAG_PACK)[:5000]
    remember_topic(story.topic)

    workdir = _new_workdir((Path.cwd() / args.out_dir).resolve(), story.topic)
    save_story(story, workdir / "story.json")
    print(f"  workdir: {workdir}")

    # 2 — narration ---------------------------------------------------------
    print("\n[2/6] Recording the narration (4 batched Gemini TTS requests) ...")
    narration, timings = _record_narration(gem, c, story.narration, workdir)
    with wave.open(str(narration), "rb") as w:
        total_len = w.getnframes() / w.getframerate()
    print(f"  narration: {total_len / 60:.1f} min")
    if total_len < 12 * 60:
        print("  [warn] shorter than 12 min — story came out thin; fine, publishing anyway.")
    if total_len > 15 * 60:
        print("  [warn] over 15:00 — YouTube accepts this only if the channel is "
              "verified at https://www.youtube.com/verify")

    # 3 — visuals -----------------------------------------------------------
    print("\n[3/6] Fetching real documentary visuals (Pexels, one scene per chapter) ...")
    build_backgrounds(gem, story, workdir, c)

    # 4 — captions ----------------------------------------------------------
    print("\n[4/6] Timing karaoke captions ...")
    (workdir / "subs.ass").write_text(build_ass(timings, c), encoding="utf-8")
    print(f"  subs.ass written ({len(timings)} lines)")

    # 5 — render ------------------------------------------------------------
    print("\n[5/6] Rendering ~15 min of video (the slow step, relax) ...")
    final = render(workdir, narration, total_len, timings, c)

    # 6 — upload ------------------------------------------------------------
    print("\n[6/6] Publishing ...")
    if args.dry_run:
        print("  --dry-run: upload skipped. Video ready at:")
        print(f"    {final}")
        return 0

    from .upload import upload_short

    result = upload_short(final, story, c)
    record_published({
        "date": dt.datetime.now().strftime("%Y-%m-%d"),
        "format": "long",
        "topic": story.topic,
        "title": story.title,
        "video_id": result["id"],
        "url": result["url"],
        "privacy": result["privacy"],
        "publishAt": result.get("publishAt", ""),
        "minutes": round(total_len / 60, 1),
    })
    print("\n" + "=" * 62)
    print(f" ✅ Story published: {result['url']}")
    if result.get("publishAt"):
        print(f"    goes public at: {result['publishAt']} UTC")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        raise SystemExit(130)
