"""Step 2 — narration with Gemini TTS, batched to respect free-tier quota.

The free tier allows only ~10 TTS requests/day/model, so we join the narration
into a few batches (default: 3 requests per video) instead of one call per
sentence. Caption timing stays well aligned: each batch's duration is split
across its sentences proportionally to their word counts, and each sentence
still gets its own start/duration for the karaoke captions.
"""
from __future__ import annotations

import hashlib
import math
import struct
import wave
from pathlib import Path

from .config import CACHE_DIR
from .gemini_client import Gemini

LEAD_S = 0.30   # silence before the first word
TAIL_S = 0.50   # silence after the last word


def _is_quota(exc: Exception) -> bool:
    s = str(exc)
    return "429" in s or "RESOURCE_EXHAUSTED" in s


def _cache_key(text: str, voice: str, style: str) -> Path:
    h = hashlib.sha1(f"{voice}|{style}|{text}".encode("utf-8")).hexdigest()[:24]
    return CACHE_DIR / f"tts_{h}.wav"


def _read_wav(path: Path) -> tuple[list[bytes], int]:
    with wave.open(str(path), "rb") as w:
        return [w.readframes(w.getnframes())], w.getframerate()


def _write_wav(path: Path, pcm: bytes, rate: int) -> None:
    """Write atomically (tmp + rename) so a crash never poisons the cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.wav")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    tmp.replace(path)


def _silence(rate: int, seconds: float) -> bytes:
    n = int(rate * seconds)
    return b"\x00\x00" * n


def _chunk_sentences(sentences: list[str], n_chunks: int) -> list[list[str]]:
    """Split into n_chunks groups of near-equal word counts, keeping order."""
    if len(sentences) <= n_chunks:
        return [[s] for s in sentences]
    weights = [max(len(s.split()), 1) for s in sentences]
    target = sum(weights) / n_chunks
    chunks: list[list[str]] = []
    cur: list[str] = []
    acc = 0.0
    for s, w in zip(sentences, weights):
        cur.append(s)
        acc += w
        if acc >= target and len(chunks) < n_chunks - 1:
            chunks.append(cur)
            cur, acc = [], 0.0
    if cur:
        chunks.append(cur)
    return chunks


def synthesize_narration(gem: Gemini, cfg: dict, sentences: list[str], workdir: Path):
    """Return (narration.wav path, [(sentence, start_s, dur_s), ...])."""
    voice = cfg["tts"]["voice"]
    style = cfg["tts"].get("style", "")
    gap = float(cfg["tts"].get("gap_seconds", 0.18))
    n_chunks = max(1, int(cfg["tts"].get("chunks", 3)))
    chunks = _chunk_sentences(sentences, n_chunks)

    print(f"  [tts] {len(sentences)} lines in {len(chunks)} batched request(s), voice={voice}")
    rate: int | None = None
    # per-chunk: (pcm, [sentence, offset_in_chunk_s, est_dur_s])
    done: list[tuple[bytes, list[tuple[str, float, float]]]] = []

    for i, chunk in enumerate(chunks):
        text = "\n".join(chunk)
        cached = _cache_key(text, voice, style)
        pcm = r = None
        if cached.exists():
            try:
                (pcm,), r = _read_wav(cached)
                print(f"    batch {i + 1}/{len(chunks)}: cached ({len(pcm) / r / 2:.1f}s)")
            except Exception:  # noqa: BLE001 — corrupt/partial cache entry
                pcm = None
                cached.unlink(missing_ok=True)
        if pcm is None:
            try:
                pcm_raw, mime_rate = gem.tts_pcm(text, voice, style)
            except Exception as exc:  # noqa: BLE001
                if _is_quota(exc):
                    raise SystemExit(
                        "\n[✗] Gemini TTS daily quota reached (free tier = 10 voice "
                        "requests/day per model).\n"
                        "    This bot is optimized to use ~3 per video — rerun after "
                        "the quota resets (midnight Pacific / ~08:00 Marrakesh),\n"
                        "    or enable pay-as-you-go billing in Google AI Studio.\n"
                        "    Note: the Google AI Pro subscription does NOT raise API "
                        "quotas — billing is separate."
                    ) from exc
                raise
            _write_wav(cached, pcm_raw, mime_rate)
            pcm, r = pcm_raw, mime_rate
            print(f"    batch {i + 1}/{len(chunks)}: synthesized ({len(pcm) / r / 2:.1f}s)")
        rate = rate or r
        if r != rate:
            raise SystemExit(f"[✗] TTS sample-rate mismatch ({r} vs {rate}) — delete state/cache and retry.")

        # split batch duration across sentences ~ proportional to word counts
        words = [max(len(s.split()), 1) for s in chunk]
        total_w = sum(words)
        dur = len(pcm) / (rate * 2)
        offsets: list[tuple[str, float, float]] = []
        pos = 0.0
        for s, w in zip(chunk, words):
            d = dur * w / total_w
            offsets.append((s, pos, d))
            pos += d
        done.append((pcm, offsets))

    if rate is None:
        raise SystemExit("[✗] No narration lines to synthesize.")

    # stitch: lead + chunk1 + gap + chunk2 ... + tail, with absolute timings
    out_pcm = bytearray(_silence(rate, LEAD_S))
    timings: list[tuple[str, float, float]] = []
    t = LEAD_S
    for i, (pcm, offsets) in enumerate(done):
        chunk_start = t
        for s, off, d in offsets:
            timings.append((s, chunk_start + off, d))
        out_pcm += pcm
        t += len(pcm) / (rate * 2)
        if i < len(done) - 1:
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
    amp = 0.28
    frames = bytearray()
    for i in range(int(seconds * rate)):
        v = amp * math.sin(2 * math.pi * freq * i / rate)
        frames += struct.pack("<h", int(v * 32767))
    _write_wav(path, bytes(frames), rate)
