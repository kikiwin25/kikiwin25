"""Step 4 — karaoke captions (.ass) with word-by-word highlight timing.

The narration timing comes from TTS (each sentence has an exact start/duration),
so we can allocate time to words proportional to their length — no speech
recognition needed. libass renders the classic CapCut-style effect: words pop
from white to yellow as they are spoken.
"""
from __future__ import annotations

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{font},{size},{primary},{secondary},{outline},&H96000000,-1,0,0,0,100,100,0,0,1,4,2,2,70,70,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _hex_bgr(rgb: str) -> str:
    """'FFEB3B' (RRGGBB) -> '&H00BBGGRR' (ASS color)."""
    rgb = rgb.strip().lstrip("#")
    if len(rgb) != 6:
        rgb = "FFFFFF"
    r, g, b = rgb[0:2], rgb[2:4], rgb[4:6]
    return f"&H00{b}{g}{r}".upper()


def _ts(seconds: float) -> str:
    s = max(0.0, seconds)
    cs = int(round(s * 100))
    return f"{cs // 360000}:{(cs // 6000) % 60:02d}:{(cs // 100) % 60:02d}.{cs % 100:02d}"


def _escape(text: str) -> str:
    return text.replace("{", "(").replace("}", ")")


def _group_words(words: list[str], max_words: int) -> list[list[str]]:
    groups, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max(1, max_words):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def _sentence_events(sentence: str, start: float, dur: float, cfg: dict) -> list[tuple[float, float, str]]:
    """Split one timed sentence into caption groups with karaoke timing."""
    max_words = int(cfg["video"].get("max_words_per_caption", 3))
    uppercase = bool(cfg["video"].get("uppercase_captions", True))

    words = sentence.split()
    if not words:
        return []
    if uppercase:
        words = [w.upper() for w in words]

    # per-word duration ~ proportional to word length (+punctuation weight)
    weights = [max(len(w.strip(".,!?;:…")), 1) + 1.0 for w in words]
    total = sum(weights)
    times = []
    t = start
    for w, wt in zip(words, weights):
        d = dur * wt / total
        times.append((w, t, d))
        t += d

    events = []
    for group in _group_words(words, max_words):
        g_words = times[: len(group)]
        times = times[len(group):]
        g_start = g_words[0][1]
        g_end = min(g_words[-1][1] + g_words[-1][2] + 0.06, start + dur + 0.35)
        text = " ".join(
            r"{\k%d}%s" % (max(1, round(d * 100)), _escape(word))
            for word, _, d in g_words
        )
        events.append((g_start, g_end, r"{\fad(70,70)}" + text))
    return events


def build_ass(timings: list[tuple[str, float, float]], cfg: dict) -> str:
    w, h = cfg["video"]["resolution"]
    cap = cfg["captions"]
    header = ASS_HEADER.format(
        w=w, h=h,
        font=cap["font"],
        size=cap["font_size"],
        primary=_hex_bgr(cap["spoken_color"]),
        secondary=_hex_bgr(cap["unspoken_color"]),
        outline=_hex_bgr(cap["outline_color"]),
        margin_v=cap["margin_v"],
    )
    events = []
    for sentence, start, dur in timings:
        for g_start, g_end, text in _sentence_events(sentence, start, dur, cfg):
            events.append(f"Dialogue: 0,{_ts(g_start)},{_ts(g_end)},Main,,0,0,0,,{text}")
    return header + "\n".join(events) + "\n"
