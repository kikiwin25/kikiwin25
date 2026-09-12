"""Orchestrator — the whole pipeline in one command.

    python -m shorts.run                 # generate + upload
    python -m shorts.run --dry-run       # generate only, skip upload
    python -m shorts.run --topic "..."   # force a topic
    python -m shorts.run --selftest      # no-API render test (beeps + gradients)
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import wave
from pathlib import Path

from .captions import build_ass
from .config import (
    env,
    load_config,
    load_used_topics,
    record_published,
    remember_topic,
)
from .gemini_client import Gemini
from .scriptgen import Script, make_script, save_script
from .assemble import render
from .tts import LEAD_S, TAIL_S, synthesize_narration, tone_wav
from .visuals import build_backgrounds


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:40] or "short").rstrip("-")


def _new_workdir(out_dir: Path, topic: str) -> Path:
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    workdir = out_dir / f"{stamp}_{_slug(topic)}"
    n = 2
    while workdir.exists():
        workdir = out_dir / f"{stamp}_{_slug(topic)}-{n}"
        n += 1
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


# ------------------------------------------------------------------ selftest
def _selftest(cfg: dict, out_dir: Path) -> int:
    print("== SELFTEST: full render path with canned data (no API calls) ==")
    script = Script(
        topic="pipeline self test",
        title="This video was made by a robot (proof)",
        hook="A robot made this entire video.",
        sentences=[
            "Every word you read is a timed caption.",
            "The voice, the timing, the visuals — all automated.",
            "Backgrounds can be AI images or clean gradients.",
            "One command turns a topic into a finished short.",
            "And GitHub Actions uploads it for me every day.",
        ],
        cta="Follow to watch it happen daily.",
        description="Self-test render of the YouTube automation pipeline.",
        tags=["automation", "test", "shorts"],
    )
    workdir = _new_workdir(out_dir, script.topic)
    save_script(script, workdir / "script.json")

    # fake narration: beeps with known durations
    rate = 24000
    durs = [2.4, 2.8, 3.2, 2.6, 3.0, 2.2][: len(script.narration)]
    timings, pcm, t = [], bytearray(), LEAD_S
    for i, (line, d) in enumerate(zip(script.narration, durs)):
        tmp = workdir / f"tone_{i}.wav"
        tone_wav(tmp, d, 392 + 60 * (i % 3), rate)
        with wave.open(str(tmp), "rb") as w:
            data = w.readframes(w.getnframes())
        pcm += data
        timings.append((line, t, d))
        t += d
        tmp.unlink()
    narration = workdir / "narration.wav"
    with wave.open(str(narration), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(LEAD_S * rate) + bytes(pcm) + b"\x00\x00" * int(TAIL_S * rate))
    total = t + TAIL_S

    build_backgrounds(None, script, workdir, cfg)
    (workdir / "subs.ass").write_text(build_ass(timings, cfg), encoding="utf-8")
    final = render(workdir, narration, total, timings, cfg)
    print(f"\n✅ Self-test passed. Watch it: {final}")
    print("   (Captions should karaoke-highlight in sync with the beeps.)")
    return 0


# --------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shorts", description="YouTube Shorts automation (Google AI)")
    ap.add_argument("--topic", default="", help="override today's topic")
    ap.add_argument("--dry-run", action="store_true", help="generate the video, skip the upload")
    ap.add_argument("--selftest", action="store_true", help="render test with canned data, no API keys")
    ap.add_argument("--out-dir", default="outputs", help="where finished videos go")
    args = ap.parse_args(argv)

    cfg = load_config()
    out_dir = (Path.cwd() / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.selftest:
        return _selftest(cfg, out_dir)

    print("=" * 62)
    print(" YouTube Shorts automation — powered by Google AI (Gemini)")
    print("=" * 62)
    api_key = env("GEMINI_API_KEY", True, "Get a free key at https://aistudio.google.com/apikey")
    gem = Gemini(api_key, cfg)

    # 1 — script -----------------------------------------------------------
    print("\n[1/6] Writing today's script ...")
    used = load_used_topics()
    script = make_script(gem, cfg, args.topic or None, used)
    remember_topic(script.topic)

    workdir = _new_workdir(out_dir, script.topic)
    save_script(script, workdir / "script.json")
    print(f"  workdir: {workdir}")

    # 2 — narration --------------------------------------------------------
    print("\n[2/6] Recording narration (Gemini TTS) ...")
    narration, timings = synthesize_narration(gem, cfg, script.narration, workdir)
    with wave.open(str(narration), "rb") as w:
        total_len = w.getnframes() / w.getframerate()

    # 3 — visuals ----------------------------------------------------------
    print("\n[3/6] Creating scene visuals ...")
    build_backgrounds(gem, script, workdir, cfg)

    # 4 — captions ---------------------------------------------------------
    print("\n[4/6] Timing karaoke captions ...")
    (workdir / "subs.ass").write_text(build_ass(timings, cfg), encoding="utf-8")
    print(f"  subs.ass written ({len(timings)} lines)")

    # 5 — render -----------------------------------------------------------
    print("\n[5/6] Rendering the video (ffmpeg) ...")
    final = render(workdir, narration, total_len, timings, cfg)

    # 6 — upload -----------------------------------------------------------
    print("\n[6/6] Publishing ...")
    if args.dry_run:
        print("  --dry-run: upload skipped. Video ready at:")
        print(f"    {final}")
        return 0

    from .upload import upload_short

    result = upload_short(final, script, cfg)
    record_published({
        "date": dt.datetime.now().strftime("%Y-%m-%d"),
        "topic": script.topic,
        "title": script.title,
        "video_id": result["id"],
        "url": result["url"],
        "privacy": result["privacy"],
        "publishAt": result.get("publishAt", ""),
        "file": str(final.relative_to(out_dir.parent)) if out_dir.parent in final.parents else str(final),
    })
    print("\n" + "=" * 62)
    print(f" ✅ Published: {result['url']}")
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
