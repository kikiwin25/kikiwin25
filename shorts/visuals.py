"""Step 3 — background visuals.

Tries Gemini image generation first (one image per scene, prompt derived from
the script). Any failure falls back to a styled gradient rendered with Pillow,
so the pipeline never breaks because of image quota.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .config import CACHE_DIR
from .gemini_client import Gemini

TARGET = (2700, 4800)  # 2.5x render size — smooth zoompan, downscaled by ffmpeg

PALETTES = [
    ((12, 16, 40), (196, 44, 92)),
    ((4, 40, 54), (34, 197, 164)),
    ((38, 12, 58), (255, 132, 60)),
    ((6, 24, 12), (120, 220, 90)),
    ((30, 8, 20), (255, 92, 128)),
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
            f"Absolutely no text, no words, no letters, no numbers, no watermark, "
            f"no logo, no borders. Leave the center-bottom area simple and clean."
        )
    return prompts


def _normalize(png_bytes: bytes, out: Path) -> None:
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    ImageOps.fit(img, TARGET, Image.LANCZOS, centering=(0.5, 0.4)).save(out, "PNG")


def _gradient(palette, out: Path, variant: int) -> None:
    """Fast vertical gradient + soft glow blob + vignette, pure Pillow."""
    top, bottom = palette
    w, h = TARGET

    col = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / (h - 1)
        col.putpixel((0, y), tuple(int(top[c] + (bottom[c] - top[c]) * t) for c in range(3)))
    base = col.resize((w, h))

    # soft glowing blob, position varies per scene
    layer = Image.new("L", TARGET, 0)
    d = ImageDraw.Draw(layer)
    cx, cy = w * (0.25 + 0.5 * (variant % 2)), h * (0.3 + 0.15 * (variant % 3))
    d.ellipse([cx - w * 0.55, cy - h * 0.22, cx + w * 0.55, cy + h * 0.22], fill=85)
    layer = layer.filter(ImageFilter.GaussianBlur(220))
    white = Image.new("RGB", TARGET, (255, 255, 255))
    base = Image.composite(white, base, layer)

    # vignette (keep center bright, darken corners gently)
    vig = ImageOps.invert(Image.radial_gradient("L")).resize(TARGET)
    black = Image.new("RGB", TARGET, (0, 0, 0))
    base = Image.composite(base, black, vig.point(lambda v: 60 + v * 195 // 255))
    base.save(out, "PNG")


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
                _normalize(cached.read_bytes(), out)
                done = True
            else:
                data = gem.image_png(prompt)
                if data:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    cached.write_bytes(data)
                    _normalize(data, out)
                    done = True
        if not done:
            _gradient(PALETTES[i % len(PALETTES)], out, i)
        print(f"  [visual] scene {i + 1}/{n}: {'gemini image' if done else 'gradient fallback'}")
        paths.append(out)
    return paths
