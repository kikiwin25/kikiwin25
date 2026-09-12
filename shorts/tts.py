"""Step 2 — narration with Gemini TTS, sentence by sentence.

Why per-sentence? Each sentence becomes its own audio file with an exact
duration — that gives us word-accurate caption timing without any
speech-recognition step. Results are cached by text hash, so re-runs
(dry-runs, failures) never burn the same quota twice.
"""
from __future__ import annotations

import hashlib
import re
import struct
import wave
from pathlib import Path

from .config import CACHE_DIR
from .gemini_client import Gemini

LEAD_S = 0.30   # silence before the first word
TAIL_S = 0.50   # silence after the last word


def _cache_key(text: str, voice: str, style: str) -> Path:
    h = hashlib.sha1(f"{voice}|{style}|{text}".encode("utf-8")).hexdigest()[:24]
    return CACHE_DIR / f"tts_{h}.wav"


def _read_wav(path: Path) -> tuple[list[bytes], int]:
    with wave.open(str(path), "rb") as w:
        return [w.readframes(w.getnframes())], w.getframerate()


def _write_wav(path: Path, pcm: bytes, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def _silence(rate: int, seconds: float) -> bytes:
    n = int(rate * seconds)
    return b"\x00\x00" * n


def synthesize_narration(gem: Gemini, cfg: dict, sentences: list[str], workdir: Path):
    """Return (narration.wav path, [(sentence, start_s, dur_s), ...])."""
    voice = cfg["tts"]["voice"]
    style = cfg["tts"].get("style", "")
    gap = float(cfg["tts"].get("gap_seconds", 0.18))

    chunks: list[bytes] = []
    rate: int | None = None
    timings: list[tuple[str, float, float]] = []

    print(f"  [tts] {len(sentences)} lines, voice={voice}")
    for i, sentence in enumerate(sentences):
        cached = _cache_key(sentence, voice, style)
        if cached.exists():
            (pcm,), r = _read_wav(cached)
        else:
            pcm_raw, mime_rate = gem.tts_pcm(sentence, voice, style)
            _write_wav(cached, pcm_raw, mime_rate)
            pcm, r = pcm_raw, mime_rate
            print(f"    line {i + 1}/{len(sentences)} synthesized ({len(pcm) / r / 2:.1f}s)")
        rate = rate or r
        if r != rate:
            raise SystemExit(f"[✗] TTS sample-rate mismatch ({r} vs {rate}) — delete state/cache and retry.")
        chunks.append((sentence, pcm))

    if rate is None:
        raise SystemExit("[✗] No narration lines to synthesize.")

    # stitch: lead + s1 + gap + s2 + ... + tail, recording exact start times
    out_pcm = bytearray(_silence(rate, LEAD_S))
    t = LEAD_S
    for i, (sentence, pcm) in enumerate(chunks):
        dur = len(pcm) / (rate * 2)
        timings.append((sentence, t, dur))
        out_pcm += pcm
        t += dur
        if i < len(chunks) - 1:
            out_pcm += _silence(rate, gap)
            t += gap
    out_pcm += _silence(rate, TAIL_S)
    total = t + TAIL_S

    final = workdir / "narration.wav"
    _write_wav(final, bytes(out_pcm), rate)
    print(f"  [tts] narration: {total:.1f}s -> {final.name}")

    if total > 58.5:
        print(f"  [warn] narration is {total:.0f}s — Shorts must stay under 60s. "
              f"Lower video.target_seconds or sentence count in config.yaml.")
    return final, timings


def tone_wav(path: Path, seconds: float, freq: int, rate: int = 24000) -> None:
    """Small helper used by --selftest: a soft sine tone."""
    import math

    amp = 0.28
    frames = bytearray()
    for i in range(int(seconds * rate)):
        v = amp * math.sin(2 * math.pi * freq * i / rate)
        frames += struct.pack("<h", int(v * 32767))
    _write_wav(path, bytes(frames), rate)
