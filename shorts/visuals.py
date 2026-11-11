"""Step 3 — background visuals.

Per-scene cascade: Pexels stock clip -> Pexels photo (real photography,
Ken Burns zoom) -> rich editorial poster rendered with Pillow. The video
never fails because one provider is down or out of quota.

NO AI images anywhere — real footage or drawn posters only.
Gemini is NOT used for images — text/script only.

Frame shape follows cfg["video"]["resolution"]: portrait 1080x1920 (Shorts,
default, unchanged) or landscape 1920x1080 (long-form) — source images are
then fitted to twice the render size for a smooth zoompan.
"""
from __future__ import annotations

import io
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .gemini_client import Gemini

TARGET = (2700, 4800)  # default 2.5x portrait render size — smooth zoompan

# Bright, high-contrast editorial palettes (top, bottom, accent)
PALETTES = [
    ((32, 44, 92), (233, 84, 128), (255, 205, 64)),    # indigo -> pink, yellow
    ((10, 78, 84), (64, 205, 191), (255, 122, 69)),    # teal -> mint, orange
    ((58, 24, 96), (150, 78, 220), (94, 234, 212)),    # purple -> violet, mint
    ((20, 60, 30), (88, 180, 92), (255, 209, 102)),    # forest -> green, sand
    ((94, 28, 34), (232, 93, 79), (255, 232, 130)),    # wine -> coral, cream
]


def _normalize(png_bytes: bytes, out: Path, target: tuple = TARGET) -> None:
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    ImageOps.fit(img, target, Image.LANCZOS, centering=(0.5, 0.4)).save(out, "PNG")


def _mean_luminance(img: Image.Image) -> float:
    small = img.convert("L").resize((48, 84))
    total = sum(small.getdata())
    return total / (48 * 84)


def math_sin(x: float) -> float:
    import math

    return 0.5 + 0.5 * math.sin(x * 6.283)


def _rich_gradient(palette, out: Path, variant: int, target: tuple = TARGET) -> None:
    """Editorial poster-style fallback: gradient + glow + stripes + halftone."""
    rng = random.Random(variant * 977 + 13)
    top, bottom, accent = palette
    w, h = target

    col = Image.new("RGB", (1, h))
    for y in range(h):
        t = y 
