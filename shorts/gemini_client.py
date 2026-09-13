"""Thin Gemini API wrapper (google-genai SDK) with retries + friendly errors.

One API key (Google AI Studio) covers every AI step:
  - limits.gemini_text_model   -> topic + script + metadata  (JSON mode)
  - limits.gemini_tts_model    -> narration audio            (raw PCM)
  - limits.gemini_image_model  -> scene backgrounds          (image bytes)
"""
from __future__ import annotations

import json
import random
import re
import time
from typing import Callable, TypeVar

T = TypeVar("T")

_RETRYABLE_TOKENS = (
    "429", "500", "502", "503", "504", "RESOURCE_EXHAUSTED", "UNAVAILABLE",
    "INTERNAL", "DEADLINE_EXCEEDED", "TIMEOUT", "RATE", "temporarily",
)


def _is_retryable(exc: Exception) -> bool:
    s = f"{type(exc).__name__}: {exc}".upper()
    return any(tok in s for tok in _RETRYABLE_TOKENS)


def with_retries(fn: Callable[[], T], attempts: int = 6, what: str = "api call") -> T:
    """Retry transient API errors patiently.

    Free-tier 429s usually clear within 30-60 s (per-minute quota), so the
    backoff now goes 10 -> 20 -> 40 -> 80 -> 90 s (~4 min total patience)
    instead of giving up after ~40 s like before.
    """
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — deliberately broad
            last = exc
            if not _is_retryable(exc) or i == attempts - 1:
                raise
            wait = min(10.0 * (2 ** i) + random.random() * 3, 90.0)
            print(f"    [retry] {what} failed ({str(exc)[:120]}) — retrying in {wait:.0f}s "
                  f"(attempt {i + 1}/{attempts})")
            time.sleep(wait)
    raise last  # pragma: no cover


class GeminiError(RuntimeError):
    pass


def parse_json_loose(raw: str) -> dict:
    """Parse a JSON object out of a model response, tolerating fences/prose."""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise GeminiError(f"Could not parse JSON from model output:\n{raw[:400]}")


class Gemini:
    def __init__(self, api_key: str, cfg: dict):
        from google import genai

        self.cfg = cfg
        self.limits = cfg["limits"]
        self.client = genai.Client(api_key=api_key)
        # image-model memoization: remember what works, skip what doesn't
        self._image_ok: tuple[str, str] | None = None
        self._image_skip: set[tuple[str, str]] = set()

    # ------------------------------------------------------------- text/JSON
    def json_text(self, prompt: str, temperature: float = 1.0) -> dict:
        model = self.limits["gemini_text_model"]

        def call() -> str:
            resp = self.client.models.generate_content(
                model=model,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "temperature": temperature,
                },
            )
            text = getattr(resp, "text", None)
            if not text:
                raise GeminiError("empty response from text model")
            return text

        return parse_json_loose(with_retries(call, what=f"{model} text"))

    # ------------------------------------------------------------------- TTS
    def tts_pcm(self, text: str, voice: str, style: str) -> tuple[bytes, int]:
        """Return (raw PCM 16-bit mono bytes, sample_rate)."""
        model = self.limits["gemini_tts_model"]
        contents = f"{style}: {text}" if style else text

        def call():
            resp = self.client.models.generate_content(
                model=model,
                contents=contents,
                config={
                    "response_modalities": ["AUDIO"],
                    "speech_config": {
                        "voice_config": {
                            "prebuilt_voice_config": {"voice_name": voice}
                        }
                    },
                },
            )
            parts = resp.candidates[0].content.parts or []
            for part in parts:
                inline = getattr(part, "inline_data", None)
                if inline and inline.data:
                    return inline.data, inline.mime_type or ""
            raise GeminiError("TTS returned no audio")

        data, mime = with_retries(call, what=f"{model} tts")
        m = re.search(r"rate=(\d+)", mime)
        return data, int(m.group(1)) if m else 24000

    # ----------------------------------------------------------------- image
    def image_png(self, prompt: str) -> bytes | None:
        """Generate one image; returns image bytes or None (poster fallback).

        "Nano Banana" (Gemini 2.5 Flash Image) first, then fallbacks.
        Memoizes the working model, skips dead ones (404), and stops early —
        so the daily image quota is never wasted on retries.
        """
        first = self.limits["gemini_image_model"]
        chain: list[tuple[str, str]] = []  # (model, method)
        # memoized winner first (0 wasted calls on scenes 2..n)
        if self._image_ok:
            chain.append(self._image_ok)
        # "Nano Banana" next (both IDs), then fallbacks
        for m in [
            "gemini-2.5-flash-image-preview",   # Nano Banana (launch ID)
            "gemini-2.5-flash-image",            # Nano Banana (stable alias)
            first or "",
            "gemini-2.0-flash-preview-image-generation",
        ]:
            if m and (m, "content") not in chain:
                chain.append((m, "content"))
        for m in ("imagen-4.0-generate-001", "imagen-3.0-generate-002"):
            if (m, "imagen") not in chain:
                chain.append((m, "imagen"))

        def _extract(resp) -> bytes:
            parts = resp.candidates[0].content.parts or []
            for part in parts:
                inline = getattr(part, "inline_data", None)
                if inline and inline.data and str(inline.mime_type or "").startswith("image/"):
                    return inline.data
            raise GeminiError(f"no image part (parts={len(parts)})")

        def _via_content(m: str, modal: bool) -> bytes:
            cfg = {"response_modalities": ["TEXT", "IMAGE"]} if modal else None
            resp = (self.client.models.generate_content(model=m, contents=prompt, config=cfg)
                    if cfg else self.client.models.generate_content(model=m, contents=prompt))
            return _extract(resp)

        def _via_imagen(m: str) -> bytes:
            r = self.client.models.generate_images(
                model=m, prompt=prompt,
                config={"number_of_images": 1, "aspect_ratio": "9:16"})
            imgs = getattr(r, "generated_images", None) or []
            if not imgs:
                raise GeminiError("no generated_images")
            return imgs[0].image.image_bytes

        errors: list[str] = []
        for m, method in chain:
            if (m, method) in self._image_skip:
                continue
            variants = ([(True,), (False,)] if method == "content" else [(None,)])
            for v in variants:
                try:
                    if method == "content":
                        data = _via_content(m, modal=v[0])
                    else:
                        data = _via_imagen(m)
                    self._image_ok = (m, method)
                    return data
                except Exception as exc:  # noqa: BLE001 — try next combo
                    label = f"{m}" + ("/modal" if v and v[0] else "")
                    errors.append(f"{label}: {str(exc)[:110]}")
                    s = str(exc).upper()
                    if "404" in s or "NOT_FOUND" in s:
                        self._image_skip.add((m, method))  # dead model, skip from now on
                        break  # model doesn't exist — no point trying its other variants
        print(f"    [image-diag] all {len(errors)} attempts failed -> poster fallback")
        for e in errors:
            print(f"      - {e}")
        return None
