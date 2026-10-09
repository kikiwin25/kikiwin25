"""Long-form audio stories — Gemini writes a ~2000-word mystery story (~15 min).

v3: the story is generated in FOUR small requests (1 outline + 3 text parts)
instead of one giant one. A 2000-word Arabic JSON exceeds Gemini's default
~8k-token output cap, which truncated the JSON mid-story and crashed the run.
v3b: the plan and each part are retried up to 3 times — Gemini occasionally
returns an empty/unparseable response, and one flaky answer used to kill
the whole run. The Story object duck-types like scriptgen.Script (it has a
`.narration` property), so the existing Pexels keyword picker works unchanged.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict

from .gemini_client import Gemini

MIN_WORDS = 1300    # below this the story is too short — regenerate manually
TARGET_WORDS = 2000  # ~14-16 min of calm Arabic narration (~130 wpm)
PART_SIZE = 4        # chapters per text request (3 parts for 11-12 chapters)
TRIES = 3            # attempts per request (Gemini flaky answers happen)


@dataclass
class Story:
    topic: str
    title: str
    hook: str
    segments: list          # [{"heading": str, "lines": [str, ...]}, ...]
    cta: str
    description: str
    tags: list[str] = field(default_factory=list)

    @property
    def narration(self) -> list[str]:
        """Every spoken line, in order (hook + all segments + cta)."""
        out = [self.hook]
        for seg in self.segments:
            out.extend(seg.get("lines", []))
        out.append(self.cta)
        return [s for s in out if s and s.strip()]

    @property
    def words(self) -> int:
        return sum(len(s.split()) for s in self.narration)


def _clean(s: str) -> str:
    return re.sub(r"[*_#`]+", "", (s or "")).strip()


HASHTAG_PACK = "#Shorts #غرائب #قصص_حقيقية #غموض #اكسبلور #لغز"

PROMPT_PLAN = """You are the head writer of a documentary audio-story channel (long-form,
15-minute vertical videos listened to like podcast episodes).
Channel niche: {niche}
Write EVERYTHING in {language}.

Task: pick ONE true, well-documented, baffling story in this niche that has NOT
been covered recently, and plan a complete ~{target} word narration for it.

Recently used topics (avoid these and anything too similar):
{used}

Return STRICT JSON with exactly this shape:
{{
  "topic": "short name of the story",
  "title": "YouTube title, max 70 chars, curiosity-gap, factual",
  "hook": "first spoken line, max 10 words, drops the listener INTO the scene",
  "chapters": [
    "chapter 1: one-line summary of what happens, 12-25 words",
    "chapter 2: one-line summary",
    "... exactly 11-12 chapters, chronological, each builds on the previous"
  ],
  "cta": "gentle closing line inviting follow, max 10 words",
  "description": "2-3 sentence YouTube description",
  "tags": ["8 to 12 search tags, single words or short phrases, no #"]
}}

PLAN RULES:
- ONE continuous true story, chronological order. Exactly 11-12 chapters.
- Chapter 1 opens in a concrete scene (time, place, a person) — NO greetings,
  NO "in this video".
- Each middle chapter adds ONE new revelation (dates, names, places, numbers,
  what witnesses said) and should end on a small cliffhanger or question.
- The LAST chapter resolves calmly: what investigators know today, what
  remains unexplained, why the story still matters. No cheap twist endings.
- Respectful, non-graphic storytelling (no gore, no suffering in detail).
- Facts must be real and verifiable; if unsure, pick a story you know well.
"""

PROMPT_PART = """You are writing the narration of a documentary audio story in {language}.
Story: {title}
Full chapter plan:
{plan}

Write the SPOKEN LINES of chapters {first} to {last} ONLY, in order:
{chapters}

Return STRICT JSON with exactly this shape:
{{
  "lines": [
    "spoken sentence 1 of chapter {first}",
    "spoken sentence 2",
    "... about 170-190 words per chapter, calm natural spoken sentences, each max 14 words"
  ]
}}

RULES:
- Calm documentary narrator voice (podcast / sleep-story energy) with rising
  tension: end each chapter on a small cliffhanger or question.
- EXCEPTION — if chapter {n_chapters} (the story's last) is in this part, it
  must resolve calmly instead: what investigators know today, what remains
  unexplained. No cheap twist endings.
- Concrete details: dates, names, places, numbers, what witnesses said.
- Respectful, non-graphic storytelling (no gore, no suffering in detail).
- Spoken natural {language}. No emojis, no stage directions, no hashtags,
  no chapter headings inside the lines, no quotation marks around the lines.
- Total for this part: 600-800 words.
"""


def _ask_json(gem: Gemini, prompt: str, what: str, validate) -> object:
    """json_text with up to TRIES attempts (Gemini sometimes answers garbage).

    SystemExit passes straight through (quota advice must never be retried).
    `validate(data)` returns True when the answer is usable.
    """
    last = "unknown error"
    for attempt in range(1, TRIES + 1):
        try:
            data = gem.json_text(prompt, temperature=1.0)
            if validate(data):
                return data
            last = f"unusable answer (failed the {what} check)"
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001 — any API hiccup is retryable
            last = f"{type(exc).__name__}: {str(exc)[:140]}"
        print(f"  [warn] {what}: attempt {attempt}/{TRIES} failed — {last}")
        if attempt < TRIES:
            time.sleep(5)
    raise SystemExit(f"[✗] {what} failed {TRIES} times. Last error: {last}")


def _enforce(story: Story, cfg: dict) -> Story:
    story.title = story.title.strip()[:100]          # YouTube hard limit
    story.description = story.description.strip()[:4900]
    if cfg["upload"].get("append_shorts_hashtag"):
        story.description = (story.description + "\n\n" + HASHTAG_PACK)[:5000]
        if "#shorts" not in story.title.lower() and len(story.title) <= 92:
            story.title += " #Shorts"
    tags, total = [], 0
    for t in story.tags:
        t = _clean(t).lstrip("#")
        if not t:
            continue
        if total + len(t) + 1 > 450:
            break
        tags.append(t)
        total += len(t) + 1
    story.tags = tags
    return story


def make_story(gem: Gemini, cfg: dict, topic_override: str | None, used: list[str]) -> Story:
    niche = cfg["channel"].get("niche", "").strip()
    if not niche:
        raise SystemExit("[✗] config.yaml → channel.niche is empty.")
    lang = cfg["channel"]["language_resolved"]
    recent = "\n".join(f"- {t}" for t in used[-40:]) or "(none yet)"

    # 1) outline -----------------------------------------------------------
    if topic_override:
        print(f"  topic (override): {topic_override}")

    def _plan_ok(data) -> bool:
        return len([_clean(str(c)) for c in (data.get("chapters") or [])
                    if _clean(str(c))]) >= 8

    plan = _ask_json(
        gem,
        PROMPT_PLAN.format(niche=niche, language=lang, used=recent, target=TARGET_WORDS),
        "story plan", _plan_ok)
    if topic_override:
        plan["topic"] = topic_override

    chapters = [_clean(str(c)) for c in (plan.get("chapters") or []) if _clean(str(c))]

    # 2) narration text, in parts small enough for the token cap ------------
    n = len(chapters)
    lines_all: list[str] = []
    for start in range(0, n, PART_SIZE):
        part_ch = chapters[start:start + PART_SIZE]
        first, last = start + 1, start + len(part_ch)
        data = _ask_json(
            gem,
            PROMPT_PART.format(
                title=_clean(str(plan.get("title", ""))),
                language=lang,
                plan="\n".join(f"{i + 1}. {c}" for i, c in enumerate(chapters)),
                first=first, last=last, n_chapters=n,
                chapters="\n".join(f"- {c}" for c in part_ch)),
            f"part {first}-{last}",
            lambda d: bool([s for s in (d.get("lines") or []) if _clean(str(s))]))
        part_lines = [_clean(str(s)) for s in (data.get("lines") or []) if _clean(str(s))]
        print(f"  part {first}-{last}: {len(part_lines)} lines")
        lines_all.extend(part_lines)

    # 3) spread the lines evenly across the chapters ------------------------
    per = max(1, round(len(lines_all) / n))
    segments = []
    for i in range(n):
        chunk = lines_all[i * per:(i + 1) * per] if i < n - 1 else lines_all[(n - 1) * per:]
        if chunk:
            segments.append({"heading": chapters[i][:80], "lines": chunk})

    story = Story(
        topic=_clean(str(plan.get("topic", "untitled")))[:120],
        title=_clean(str(plan.get("title", plan.get("topic", "Untitled")))),
        hook=_clean(str(plan.get("hook", ""))),
        segments=segments,
        cta=_clean(str(plan.get("cta", "Follow for more stories."))),
        description=_clean(str(plan.get("description", ""))),
        tags=[_clean(str(t)) for t in (plan.get("tags") or [])],
    )

    if len(story.segments) < 8:
        raise SystemExit(f"[✗] Story assembled with too few segments "
                         f"({len(story.segments)}) — raw plan:\n{json.dumps(plan)[:500]}")
    if story.words < MIN_WORDS:
        raise SystemExit(f"[✗] Story too short: {story.words} words (need ≥ {MIN_WORDS}). "
                         f"Check the 'part X-Y: N lines' prints above to find the thin part.")
    story = _enforce(story, cfg)

    print(f"  topic:     {story.topic}")
    print(f"  title:     {story.title}")
    print(f"  hook:      {story.hook}")
    print(f"  segments:  {len(story.segments)}")
    print(f"  narration: {len(story.narration)} lines, {story.words} words "
          f"(~{story.words / 130:.0f}-{story.words / 110:.0f} min)")
    return story


def save_story(story: Story, path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(story), f, ensure_ascii=False, indent=2)
