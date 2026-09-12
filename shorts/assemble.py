"""Step 5 — assemble the vertical Short with ffmpeg + libass.

Pipeline:  bg_i.png --zoompan--> scene_i.mp4 --concat--> silent.mp4
           narration.wav (+optional music) + subs.ass  -->  final.mp4
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

W, H = 1080, 1920


# ------------------------------------------------------------------ ffmpeg
def find_ffmpeg() -> str:
    """System ffmpeg first; imageio-ffmpeg static build as fallback."""
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise SystemExit(
            "[✗] ffmpeg not found. Install it (apt/brew/choco) or run:\n"
            "    pip install imageio-ffmpeg"
        )


def _run(cmd: list[str], cwd: Path) -> None:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-25:])
        raise SystemExit(f"[✗] ffmpeg failed:\n{' '.join(cmd[:8])} ...\n{tail}")


# --------------------------------------------------------------- zoompan
# p = frame progress 0..1 (on/total). Centered crop keeps motion smooth.
_PROFILES = [
    ("zoom in",  "1+0.12*on/{n}",          "iw/2-(iw/zoom/2)",        "ih/2-(ih/zoom/2)"),
    ("pan L->R", "1.12",                    "(iw-iw/zoom)*on/{n}",     "ih/2-(ih/zoom/2)"),
    ("zoom out", "1.12-0.12*on/{n}",       "iw/2-(iw/zoom/2)",        "ih/2-(ih/zoom/2)"),
    ("pan R->L", "1.12",                    "(iw-iw/zoom)*(1-on/{n})", "ih/2-(ih/zoom/2)"),
]


def _render_scene(ffmpeg: str, bg: Path, frames: int, fps: int, idx: int, workdir: Path) -> Path:
    name, zf, xf, yf = _PROFILES[idx % len(_PROFILES)]
    n = max(frames - 1, 1)
    z, x, y = zf.format(n=n), xf.format(n=n), yf.format(n=n)
    vf = (
        f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,"
        f"crop={W * 2}:{H * 2},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={W}x{H}:fps={fps},"
        f"format=yuv420p"
    )
    out = workdir / f"scene_{idx}.mp4"
    _run([ffmpeg, "-y", "-i", bg.name, "-vf", vf, "-frames:v", str(frames),
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", out.name], workdir)
    return out


def _concat(ffmpeg: str, scenes: list[Path], workdir: Path) -> Path:
    list_file = workdir / "scenes.txt"
    list_file.write_text("".join(f"file '{p.name}'\n" for p in scenes), encoding="utf-8")
    silent = workdir / "silent.mp4"
    _run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", list_file.name,
          "-c", "copy", silent.name], workdir)
    return silent


def _mux(ffmpeg: str, silent: Path, narration: Path, subs: Path, workdir: Path,
         cfg: dict) -> Path:
    final = workdir / "final.mp4"
    music_cfg = cfg.get("music", {})
    music_path = (workdir.parent.parent / music_cfg["file"]).resolve() \
        if music_cfg.get("enabled") else None
    if music_path and not music_path.exists():
        print(f"  [warn] music enabled but {music_path} missing — skipping music")
        music_path = None

    # ass filter + bundled repo fonts (e.g. Arabic captions with Almarai)
    ass_filter = f"ass={subs.name}"
    repo_fonts = workdir.parent.parent / "fonts"
    if repo_fonts.is_dir():
        import os

        rel = os.path.relpath(repo_fonts, workdir)
        ass_filter = f"ass={subs.name}:fontsdir={rel}"

    common = ["-map", "0:v", "-map", "[a]", "-vf", ass_filter,
              "-c:v", "libx264", "-preset", "medium", "-crf", "19",
              "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
              "-movflags", "+faststart", "-shortest", final.name]

    if music_path:
        fc = (f"[2:a]loudnorm=I=-14:TP=-1.5:LRA=11[vn];"
              f"[1:a]volume={float(music_cfg.get('volume', 0.08))}[mus];"
              f"[vn][mus]amix=inputs=2:duration=first:normalize=0[a]")
        _run([ffmpeg, "-y", "-i", silent.name, "-stream_loop", "-1",
              str(music_path), "-i", narration.name, "-filter_complex", fc, *common],
             workdir)
    else:
        fc = "[1:a]loudnorm=I=-14:TP=-1.5:LRA=11[a]"
        _run([ffmpeg, "-y", "-i", silent.name, "-i", narration.name,
              "-filter_complex", fc, *common], workdir)
    return final


# ------------------------------------------------------------------ entry
def render(workdir: Path, narration: Path, narration_len_s: float,
           timings, cfg: dict) -> Path:
    """Build final.mp4 inside workdir. `timings` only used for subs filename."""
    ffmpeg = find_ffmpeg()
    w, h = cfg["video"]["resolution"]
    fps = int(cfg["video"]["fps"])

    # subs file is written by caller into workdir/subs.ass
    subs = workdir / "subs.ass"
    if not subs.exists():
        raise SystemExit("[✗] subs.ass missing — captions step did not run.")

    total_frames = max(fps, round(narration_len_s * fps))
    n_scenes = max(1, len(list(workdir.glob("bg_*.png"))))
    base = total_frames // n_scenes
    extra = total_frames - base * n_scenes

    scenes = []
    idx = 0
    for i in range(n_scenes):
        frames = base + (1 if i < extra else 0)
        bg = workdir / f"bg_{i}.png"
        scenes.append(_render_scene(ffmpeg, bg, frames, fps, idx, workdir))
        idx += 1
        print(f"  [render] scene {i + 1}/{n_scenes} ({frames / fps:.1f}s)")

    silent = _concat(ffmpeg, scenes, workdir)
    print("  [render] scenes concatenated, muxing audio + captions ...")
    final = _mux(ffmpeg, silent, narration, subs, workdir, cfg)
    size_mb = final.stat().st_size / 1e6
    print(f"  [render] DONE -> {final}  ({size_mb:.1f} MB)")
    return final
