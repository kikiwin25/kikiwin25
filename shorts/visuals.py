"""Step 3 — background visuals.

Per-scene cascade: Pexels stock clip -> Pexels photo (real photography,
Ken Burns zoom) -> Pollinations free AI image (no key, no quota) -> rich
editorial poster rendered with Pillow. The video never fails because one
provider is down or out of quota.

Gemini is NOT used for images anymore — text/script only.

Frame shape follows cfg["video"]["resolution"]: portrait 1080x1920 (Shorts,
default, unchanged) or landscape 1920x1080 (long-form) — source images are
then fitted to twice the render size for a smooth zoompan.
"""
from __future__ import annotations

import hashlib
import io
import random
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .config import CACHE_DIR
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


def scene_prompts(script, n_scenes: int, cfg: dict) -> list[str]:
    """Split the narration into n scene descriptions."""
    lines = script.narration
    per = max(1, (len(lines) + n_scenes - 1) // n_scenes)
    style = cfg["video"].get("scene_style", "cinematic, vibrant")
    res = list(cfg["video"].get("resolution") or [1080, 1920])
    shape = ("Horizontal 16:9 cinematic widescreen" if int(res[0]) > int(res[1])
             else "Vertical 9:16")
    prompts = []
    for i in range(n_scenes):
        excerpt = " ".join(lines[i * per : (i + 1) * per])[:300]
        prompts.append(
            f"{shape} background illustration for a video scene. "
            f"Scene concept: {excerpt}. Style: {style}. "
            f"Bright, colorful, eye-catching, full of detail across the whole frame. "
            f"Absolutely no text, no words, no letters, no numbers, no watermark, "
            f"no logo, no borders. Leave the center-bottom area simple and clean."
        )
    return prompts


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


def _pollinations_image(prompt: str, seed: int, pw: int = 864, ph: int = 1536) -> bytes | None:
    """Free AI image via pollinations.ai — no API key, no quota, no signup."""
    url = ("https://image.pollinations.ai/prompt/"
           + urllib.parse.quote(prompt[:600])
           + f"?width={pw}&height={ph}&nologo=true&seed={seed}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        if len(data) < 10_000:  # too small = error page, not a real image
            print(f"    [pollinations] response too small ({len(data)} B)")
            return None
        return data
    except Exception as exc:  # noqa: BLE001 — best-effort provider
        print(f"    [pollinations] failed ({str(exc)[:100]})")
        return None


def _rich_gradient(palette, out: Path, variant: int, target: tuple = TARGET) -> None:
    """Editorial poster-style fallback: gradient + glow + stripes + halftone."""
    rng = random.Random(variant * 977 + 13)
    top, bottom, accent = palette
    w, h = target

    col = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / (h - 1)
        col.putpixel((0, y), tuple(int(top[c] + (bottom[c] - top[c]) * t) for c in range(3)))
    base = col.resize((w, h)).convert("RGB")

    glow = Image.new("L", target, 0)
    d = ImageDraw.Draw(glow)
    cx, cy, r = w * (0.3 + 0.4 * (variant % 2)), h * (0.30 + 0.06 * (variant % 3)), w * 0.34
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=110)
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    base = Image.composite(Image.new("RGB", target, accent), base, glow)

    stripes = Image.new("L", target, 0)
    ds = ImageDraw.Draw(stripes)
    band = w // 9
    for k in range(-6, 14):
        x0 = k * band * 2 + (variant % 3) * band
        ds.polygon([(x0, h), (x0 + band, h), (x0 + band + int(w * 0.5), 0), (x0 + int(w * 0.5), 0)],
                   fill=46)
    stripes = stripes.filter(ImageFilter.GaussianBlur(3))
    base = Image.composite(Image.new("RGB", target, (255, 255, 255)), base, stripes)

    dots = Image.new("L", target, 0)
    dd = ImageDraw.Draw(dots)
    step = w // 30
    for yy in range(step // 2, h, step):
        for xx in range(step // 2, w, step):
            rr = max(2, int(step * 0.16 * (1 + math_sin(yy / h))))
            dd.ellipse([xx - rr, yy - rr, xx + rr, yy + rr], fill=26)
    base = Image.composite(Image.new("RGB", target, (255, 255, 255)), base,
                           dots.filter(ImageFilter.GaussianBlur(1)))

    vig = ImageOps.invert(Image.radial_gradient("L")).resize(target)
    black = Image.new("RGB", target, (0, 0, 0))
    base = Image.composite(base, black, vig.point(lambda v: 120 + v * 135 // 255))

    lum = _mean_luminance(base)
    if lum < 70:
        from PIL import ImageEnhance

        base = ImageEnhance.Brightness(base).enhance(1.35)
        lum = _mean_luminance(base)
        print(f"    [visual] brightened -> {lum:.0f}/255")
    base.save(out, "PNG")
    print(f"    [visual] fallback poster luminance: {lum:.0f}/255")


def build_backgrounds(gem: Gemini | None, script, workdir: Path, cfg: dict) -> list[Path]:
    """Per scene: Pexels clip -> Pexels photo -> Pollinations AI image -> poster.

    Gemini is never called for images (text/script only). The `gem` argument
    is kept for call-compat (stock keywords are picked locally).
    """
    from .pexels import (fetch_pexels_video, fetch_pexels_photo,
                         keywords_for_scene)  # lazy import

    n = max(1, min(int(cfg["video"].get("scenes", 3)), int(cfg["limits"].get("max_images", 3))))
    use_stock = bool((cfg["video"].get("stock_video") or {}).get("enabled", True))
    res = list(cfg["video"].get("resolution") or [1080, 1920])
    if int(res[0]) > int(res[1]):              # 16:9 landscape long-form
        target = (int(res[0]) * 2, int(res[1]) * 2)
        polli = (1536, 864)
    else:                                      # 9:16 portrait Shorts — unchanged
        target = TARGET
        polli = (864, 1536)
    prompts = scene_prompts(script, n, cfg)
    paths: list[Path] = []
    stock_count = img_count = 0

    for i, prompt in enumerate(prompts):
        out = workdir / f"bg_{i}"

        # ---- 1) Pexels stock clip ----
        if use_stock:
            kws = keywords_for_scene(gem, script, i, n)
            print(f"    [scene {i + 1}] stock keywords: {kws}")
            clip = fetch_pexels_video(kws, i, out.with_suffix(".mp4"), cfg)
            if clip:
                paths.append(clip)
                stock_count += 1
                print(f"  [visual] scene {i + 1}/{n}: pexels stock clip")
                continue

            # ---- 1b) Pexels PHOTO (real photography, Ken Burns zoom) ----
            jpg = out.with_suffix(".jpg")
            if fetch_pexels_photo(kws, i, jpg, cfg):
                try:
                    _normalize(jpg.read_bytes(), out.with_suffix(".png"), target)
                    jpg.unlink(missing_ok=True)
                    paths.append(out.with_suffix(".png"))
                    stock_count += 1
                    print(f"  [visual] scene {i + 1}/{n}: pexels photo")
                    continue
                except Exception as exc:  # noqa: BLE001 — bad file, keep cascading
                    print(f"    [warn] pexels photo unusable ({str(exc)[:80]})")

        # ---- 2) cached Pollinations image ----
        h = hashlib.sha1(prompt.encode()).hexdigest()[:24]
        cached = CACHE_DIR / f"img_{h}.png"
        done = False
        if cached.exists():
            try:
                _normalize(cached.read_bytes(), out.with_suffix(".png"), target)
                done = True
                print(f"  [visual] scene {i + 1}/{n}: pollinations image (cache)")
            except Exception:  # noqa: BLE001 — corrupt cache
                cached.unlink(missing_ok=True)

        # ---- 3) Pollinations free AI image (no key, no quota) ----
        if not done:
            print(f"    [scene {i + 1}] pollinations image...")
            data = _pollinations_image(prompt, seed=i * 17 + 3, pw=polli[0], ph=polli[1])
            if data:
                try:
                    _normalize(data, out.with_suffix(".png"), target)
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    cached.write_bytes(data)
                    done = True
                except Exception as exc:  # noqa: BLE001 — unreadable image
                    print(f"    [warn] pollinations image unusable ({str(exc)[:80]})")

        if done:
            paths.append(out.with_suffix(".png"))
            img_count += 1
            print(f"  [visual] scene {i + 1}/{n}: pollinations image")
            continue

        # ---- 4) Editorial poster ----
        _rich_gradient(PALETTES[i % len(PALETTES)], out.with_suffix(".png"), i, target)
        print(f"  [visual] scene {i + 1}/{n}: editorial fallback poster")
        paths.append(out.with_suffix(".png"))

    # safety net: nearly-black images get replaced (stock clips skipped)
    for i, p in enumerate(paths):
        if p.suffix != ".png":
            continue
        img = Image.open(p).convert("RGB")
        lum = _mean_luminance(img)
        if lum < 22:
            print(f"    [warn] bg_{i} too dark ({lum:.0f}/255) — regenerating poster")
            _rich_gradient(PALETTES[i % len(PALETTES)], p, i + 1, target)
    print(f"  [visual] summary: {stock_count} stock / {img_count} AI / "
          f"{n - stock_count - img_count} posters")
    return paths
