"""Step 3 — background visuals.

Tries Gemini image generation first (one image per scene, prompt derived from
the script). Any failure falls back to a rich editorial-style graphic rendered
with Pillow (bright palettes, bold shapes, halftone texture), so the video
never ends up with a plain dark background.
"""
from __future__ import annotations

import hashlib
import io
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .config import CACHE_DIR
from .gemini_client import Gemini

TARGET = (2700, 4800)  # 2.5x render size — smooth zoompan, downscaled by ffmpeg

# Bright, high-contrast editorial palettes (top, bottom, accent)
PALETTES = [
    ((32, 44, 92), (233, 84, 128), (255, 205, 64)),    # indigo -> pink, yellow
    ((10, 78, 84), (64, 205, 191), (255, 122, 69)),    # teal -> mint, orange
    ((58, 24, 96), (150, 78, 220), (94, 234, 212)),    # purple -> violet, mint
    ((20, 60, 30), (88, 180, 92), (255, 209, 102)),    # forest -> green, sand
    ((94, 28, 34), (232, 93, 79), (255, 232, 130)),    # wine -> coral, cream
]


def scene_prompts(script, n_scenes: int, cfg: dict) -> list[str]:
    """Split the narration into n scene descriptions."""
    lines = script.narration
    per = max(1, (len(lines) + n_scenes - 1) // n_scenes)
    style = cfg["video"].get("scene_style", "cinematic, vibrant")
    prompts = []
    for i in range(n_scenes):
        excerpt = " ".join(lines[i * per : (i + 1) * per])[:300]
        prompts.append(
            f"Vertical 9:16 background illustration for a short video scene. "
            f"Scene concept: {excerpt}. Style: {style}. "
            f"Bright, colorful, eye-catching, full of detail across the whole frame. "
            f"Absolutely no text, no words, no letters, no numbers, no watermark, "
            f"no logo, no borders. Leave the center-bottom area simple and clean."
        )
    return prompts


def _normalize(png_bytes: bytes, out: Path) -> None:
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    ImageOps.fit(img, TARGET, Image.LANCZOS, centering=(0.5, 0.4)).save(out, "PNG")


def _mean_luminance(img: Image.Image) -> float:
    small = img.convert("L").resize((48, 84))
    total = sum(small.getdata())
    return total / (48 * 84)


def math_sin(x: float) -> float:
    import math

    return 0.5 + 0.5 * math.sin(x * 6.283)


def _rich_gradient(palette, out: Path, variant: int) -> None:
    """Editorial poster-style fallback: gradient + glow + stripes + halftone."""
    rng = random.Random(variant * 977 + 13)
    top, bottom, accent = palette
    w, h = TARGET

    # 1) vertical gradient (1-column strip, then resize = fast & smooth)
    col = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / (h - 1)
        col.putpixel((0, y), tuple(int(top[c] + (bottom[c] - top[c]) * t) for c in range(3)))
    base = col.resize((w, h)).convert("RGB")

    # 2) big glowing circle (the "sun" — gives the poster a focal point)
    glow = Image.new("L", TARGET, 0)
    d = ImageDraw.Draw(glow)
    cx, cy, r = w * (0.3 + 0.4 * (variant % 2)), h * (0.30 + 0.06 * (variant % 3)), w * 0.34
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=110)
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    base = Image.composite(Image.new("RGB", TARGET, accent), base, glow)

    # 3) bold diagonal stripes (Vox-poster energy)
    stripes = Image.new("L", TARGET, 0)
    ds = ImageDraw.Draw(stripes)
    band = w // 9
    for k in range(-6, 14):
        x0 = k * band * 2 + (variant % 3) * band
        ds.polygon([(x0, h), (x0 + band, h), (x0 + band + int(w * 0.5), 0), (x0 + int(w * 0.5), 0)],
                   fill=46)
    stripes = stripes.filter(ImageFilter.GaussianBlur(3))
    base = Image.composite(Image.new("RGB", TARGET, (255, 255, 255)), base, stripes)

    # 4) halftone dot texture (editorial print feel)
    dots = Image.new("L", TARGET, 0)
    dd = ImageDraw.Draw(dots)
    step = w // 30
    for yy in range(step // 2, h, step):
        for xx in range(step // 2, w, step):
            rr = max(2, int(step * 0.16 * (1 + math_sin(yy / h))))
            dd.ellipse([xx - rr, yy - rr, xx + rr, yy + rr], fill=26)
    base = Image.composite(Image.new("RGB", TARGET, (255, 255, 255)), base,
                           dots.filter(ImageFilter.GaussianBlur(1)))

    # 5) gentle vignette so captions pop, but keep the frame bright
    vig = ImageOps.invert(Image.radial_gradient("L")).resize(TARGET)
    black = Image.new("RGB", TARGET, (0, 0, 0))
    base = Image.composite(base, black, vig.point(lambda v: 120 + v * 135 // 255))

    lum = _mean_luminance(base)
    if lum < 70:  # safety: brighten dim posters
        from PIL import ImageEnhance

        base = ImageEnhance.Brightness(base).enhance(1.35)
        lum = _mean_luminance(base)
        print(f"    [visual] brightened -> {lum:.0f}/255")
    base.save(out, "PNG")
    print(f"    [visual] fallback poster luminance: {lum:.0f}/255")


def build_backgrounds(gem: Gemini | None, script, workdir: Path, cfg: dict) -> list[Path]:
    n = max(1, min(int(cfg["video"].get("scenes", 3)), int(cfg["limits"].get("max_images", 3))))
    prompts = scene_prompts(script, n, cfg)
    paths: list[Path] = []

    for i, prompt in enumerate(prompts):
        out = workdir / f"bg_{i}.png"
        h = hashlib.sha1(prompt.encode()).hexdigest()[:24]
        cached = CACHE_DIR / f"img_{h}.png"
        done = False
        if gem is not None:
            if cached.exists():
                try:
                    _normalize(cached.read_bytes(), out)
                    done = True
                except Exception:  # noqa: BLE001 — corrupt cache
                    cached.unlink(missing_ok=True)
            if not done:
                data = gem.image_png(prompt)
                if data:
                    try:
                        CACHE_DIR.mkdir(parents=True, exist_ok=True)
                        cached.write_bytes(data)
                        _normalize(data, out)
                        done = True
                    except Exception as exc:  # noqa: BLE001 — unreadable image
                        print(f"    [warn] generated image unusable ({str(exc)[:100]})")
        if not done:
            _rich_gradient(PALETTES[i % len(PALETTES)], out, i)
            print(f"  [visual] scene {i + 1}/{n}: editorial fallback poster")
        else:
            print(f"  [visual] scene {i + 1}/{n}: gemini image")
        paths.append(out)

    # safety net: if any background is still nearly black, replace it
    for i, p in enumerate(paths):
        img = Image.open(p).convert("RGB")
        lum = _mean_luminance(img)
        if lum < 22:
            print(f"    [warn] bg_{i} too dark ({lum:.0f}/255) — regenerating poster")
            _rich_gradient(PALETTES[i % len(PALETTES)], p, i + 1)
    return paths
