"""Step 2 — narration. Two interchangeable voice providers:

  1. ElevenLabs  (tts.provider: "elevenlabs")
     - Most natural voices, excellent Arabic (eleven_multilingual_v2)
     - 1 request per video, returns WORD-LEVEL timestamps -> exact captions
     - Needs ELEVENLABS_API_KEY (free tier ~10k credits/month)

  2. Gemini TTS  (tts.provider: "gemini") — Google AI, batched (3 req/video)

Automatic resilience: if ElevenLabs is missing/unconfigured/out of quota,
the pipeline silently falls back to Gemini TTS, and vice-versa via config.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import struct
import urllib.request
import wave
from pathlib import Path

from .config import CACHE_DIR
from .gemini_client import Gemini

LEAD_S = 0.30   # silence before the first word
TAIL_S = 0.50   # silence after the last word


class ProviderError(RuntimeError):
    pass


def _is_quota(exc: Exception) -> bool:
    s = str(exc)
    return "429" in s or "RESOURCE_EXHAUSTED" in s


# ------------------------------------------------------------------ wav io
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


def tone_wav(path: Path, seconds: float, freq: int, rate: int = 24000) -> None:
    """Small helper used by --selftest: a soft sine tone."""
    amp = 0.28
    frames = bytearray()
    for i in range(int(seconds * rate)):
        v = amp * math.sin(2 * math.pi * freq * i / rate)
        frames += struct.pack("<h", int(v * 32767))
    _write_wav(path, bytes(frames), rate)


# ------------------------------------------------- stitching (shared)
def _stitch(rate: int, chunks: list[tuple[bytes, list[tuple[str, float, float]]]],
            gap: float, workdir: Path):
    """chunks: [(pcm, [(sentence, offset_in_chunk_s, dur_s), ...]), ...]"""
    out_pcm = bytearray(_silence(rate, LEAD_S))
    timings: list[tuple[str, float, float]] = []
    t = LEAD_S
    for i, (pcm, offsets) in enumerate(chunks):
        chunk_start = t
        for s, off, d in offsets:
            timings.append((s, chunk_start + off, d))
        out_pcm += pcm
        t += len(pcm) / (rate * 2)
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


# ============================================================ ELEVENLABS
EL_BASE = "https://api.elevenlabs.io/v1/text-to-speech"


def _el_call(url: str, payload: dict, api_key: str, timeout: int = 180) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"xi-api-key": api_key, "Content-Type": "application/json",
                 "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _words_from_alignment(align: dict) -> list[tuple[float, float]]:
    """(start, end) per word, from ElevenLabs character alignment."""
    chars = align.get("characters") or []
    starts = align.get("character_start_times_seconds") or []
    ends = align.get("character_end_times_seconds") or []
    words: list[tuple[float, float]] = []
    cur: list[float] | None = None
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if cur:
                words.append((cur[0], cur[1]))
                cur = None
        else:
            if cur is None:
                cur = [float(s), float(e)]
            else:
                cur[1] = float(e)
    if cur:
        words.append((cur[0], cur[1]))
    return words


def _synthesize_elevenlabs(cfg: dict, sentences: list[str], workdir: Path):
    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise ProviderError("ELEVENLABS_API_KEY not set — falling back to Gemini TTS")

    voice_id = str(cfg["tts"].get("elevenlabs_voice_id", "pNInz6obpgDQGcFmaJgB"))
    model = str(cfg["tts"].get("elevenlabs_model", "eleven_multilingual_v2"))
    rate = 24000
    text = "\n".join(sentences)

    h = hashlib.sha1(f"EL|{voice_id}|{model}|{text}".encode("utf-8")).hexdigest()[:24]
    cached = CACHE_DIR / f"el_{h}.json"
    if cached.exists():
        try:
            data = json.loads(cached.read_text(encoding="utf-8"))
            pcm = base64.b64decode(data["audio"])
            align = data.get("alignment") or {}
            print(f"  [tts-elevenlabs] cached ({len(pcm) / (rate * 2):.1f}s)")
        except Exception:  # noqa: BLE001
            pcm, align = None, {}
    else:
        pcm, align = None, {}

    if pcm is None:
        print(f"  [tts-elevenlabs] 1 request: {len(sentences)} lines, "
              f"voice={voice_id}, model={model}")
        try:
            data = _el_call(
                f"{EL_BASE}/{voice_id}/with-timestamps?output_format=pcm_24000",
                {
                    "text": text,
                    "model_id": model,
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                },
                api_key,
            )
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "code", None)
            body = ""
            try:
                body = exc.read().decode("utf-8", "ignore")[:200]  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass
            raise ProviderError(f"ElevenLabs API error ({code}): {body or exc}")
        pcm = base64.b64decode(data["audio_base64"])
        align = data.get("alignment") or {}
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_suffix(".tmp.json")
        tmp.write_text(json.dumps({"audio": data["audio_base64"], "alignment": align}),
                       encoding="utf-8")
        tmp.replace(cached)
        print(f"  [tts-elevenlabs] synthesized ({len(pcm) / (rate * 2):.1f}s)")

    # exact word timings from the alignment -> exact sentence timings
    words = _words_from_alignment(align)
    if not words:
        raise ProviderError("ElevenLabs returned no alignment — falling back")

    timings: list[tuple[str, float, float]] = []
    wi = 0
    for sentence in sentences:
        n = max(len(sentence.split()), 1)
        group = words[wi : wi + n]
        wi += n
        if group:
            timings.append((sentence, group[0][0], group[-1][1] - group[0][0]))
    if wi < len(words) and timings:  # leftover words -> extend last sentence
        s, st, _ = timings[-1]
        timings[-1] = (s, st, words[-1][1] - st)

    final = workdir / "narration.wav"
    _write_wav(final, pcm, rate)
    total = len(pcm) / (rate * 2)
    print(f"  [tts-elevenlabs] narration: {total:.1f}s -> {final.name} "
          f"(word-exact timing, {len(words)} words)")
    if total > 58.5:
        print(f"  [warn] narration is {total:.0f}s — Shorts must stay under 60s.")
    return final, timings


# =============================================================== GEMINI
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


def _cache_key(text: str, voice: str, style: str) -> Path:
    h = hashlib.sha1(f"{voice}|{style}|{text}".encode("utf-8")).hexdigest()[:24]
    return CACHE_DIR / f"tts_{h}.wav"


def _synthesize_gemini(gem: Gemini, cfg: dict, sentences: list[str], workdir: Path):
    voice = cfg["tts"]["voice"]
    style = cfg["tts"].get("style", "")
    gap = float(cfg["tts"].get("gap_seconds", 0.18))
    n_chunks = max(1, int(cfg["tts"].get("chunks", 3)))
    chunks = _chunk_sentences(sentences, n_chunks)

    print(f"  [tts] {len(sentences)} lines in {len(chunks)} batched request(s), voice={voice}")
    rate: int | None = None
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
                        "    This bot uses ~3 per video — rerun after the quota resets "
                        "(midnight Pacific / ~08:00 Marrakesh),\n"
                        "    configure ElevenLabs (tts.provider), or enable pay-as-you-go "
                        "billing in Google AI Studio."
                    ) from exc
                raise
            _write_wav(cached, pcm_raw, mime_rate)
            pcm, r = pcm_raw, mime_rate
            print(f"    batch {i + 1}/{len(chunks)}: synthesized ({len(pcm) / r / 2:.1f}s)")
        rate = rate or r
        if r != rate:
            raise SystemExit(f"[✗] TTS sample-rate mismatch ({r} vs {rate}) — delete state/cache and retry.")

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
    return _stitch(rate, done, gap, workdir)


# ================================================================ ENTRY
def synthesize_narration(gem: Gemini, cfg: dict, sentences: list[str], workdir: Path):
    """Dispatch on tts.provider. ElevenLabs -> exact captions; Gemini fallback."""
    provider = str(cfg["tts"].get("provider", "gemini")).lower().strip()
    if provider in ("elevenlabs", "eleven_labs", "11labs", "el"):
        try:
            return _synthesize_elevenlabs(cfg, sentences, workdir)
        except ProviderError as exc:
            print(f"  [warn] {exc}")
    return _synthesize_gemini(gem, cfg, sentences, workdir)
