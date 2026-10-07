"""Long-form audio stories — Gemini writes a ~2000-word mystery story (~15 min).

The Story object duck-types like scriptgen.Script (it has a `.narration`
property), so the existing Pexels keyword picker works on it unchanged.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict

from .gemini_client import Gemini

MIN_WORDS = 1300    # below this the story is too short — regenerate manually
TARGET_WORDS = 2000  # ~14-16 min of calm Arabic narration (~130 wpm)


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

PROMPT = """You are the head writer of a documentary audio-story channel (long-form,
15-minute vertical videos listened to like podcast episodes).
Channel niche: {niche}
Write EVERYTHING in {language}.

Task: pick ONE true, well-documented, baffling story in this niche that has NOT
been covered recently, and write a complete ~{target} word narration for it.

Recently used topics (avoid these and anything too similar):
{used}

Return STRICT JSON with exactly this shape:
{{
  "topic": "short name of the story",
  "title": "YouTube title, max 70 chars, curiosity-gap, factual",
  "hook": "first spoken line, max 10 words, drops the listener INTO the scene",
  "segments": [
    {{"heading": "short chapter name",
      "lines": ["10-14 calm spoken sentences, each max 14 words",
                "about 170-190 words per segment"]}}
  ],
  "cta": "gentle closing line inviting follow, max 10 words",
  "description": "2-3 sentence YouTube description",
  "tags": ["8 to 12 search tags, single words or short phrases, no #"]
}}

STORY RULES:
- Exactly 11-12 segments. ONE continuous true story, chronological order.
- Calm documentary narrator voice (podcast / sleep-story energy) but with
  rising tension: every segment must END on a small cliffhanger or question
  that forces the listener to keep going.
- Segment 1 opens in a concrete scene (time, place, a person). NO greetings,
  NO "in this video", NO announcements.
- Middle segments: build the mystery with concrete details — dates, names,
  places, numbers, what witnesses said. One new revelation per segment.
- The FINAL segment resolves calmly: what investigators know today, what
  remains unexplained, why the story still matters. No cheap twist endings.
- Respectful, non-graphic storytelling (no gore, no suffering in detail).
- Facts must be real and verifiable; if unsure, pick a story you know well.
- Spoken natural {language}. No emojis, no stage directions, no hashtags
  inside spoken lines, no quotation marks around the lines.
- Total narration (hook + all segments + cta) must be 1800-2200 words.
"""


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

    if topic_override:
        print(f"  topic (override): {topic_override}")
    prompt = PROMPT.format(niche=niche, language=lang, used=recent, target=TARGET_WORDS)
    data = gem.json_text(prompt, temperature=1.0)

    if topic_override:
        data["topic"] = topic_override

    segments = []
    for seg in (data.get("segments") or []):
        segments.append({
            "heading": _clean(str(seg.get("heading", "")))[:80],
            "lines": [_clean(str(s)) for s in (seg.get("lines") or []) if _clean(str(s))],
        })
    segments = [s for s in segments if s["lines"]]

    story = Story(
        topic=_clean(str(data.get("topic", "untitled")))[:120],
        title=_clean(str(data.get("title", data.get("topic", "Untitled")))),
        hook=_clean(str(data.get("hook", ""))),
        segments=segments,
        cta=_clean(str(data.get("cta", "Follow for more stories."))),
        description=_clean(str(data.get("description", ""))),
        tags=[_clean(str(t)) for t in (data.get("tags") or [])],
    )

    if len(story.segments) < 8:
        raise SystemExit(f"[✗] Story came back with too few segments "
                         f"({len(story.segments)}) — raw output:\n{json.dumps(data)[:500]}")
    if story.words < MIN_WORDS:
        raise SystemExit(f"[✗] Story too short: {story.words} words (need ≥ {MIN_WORDS}) "
                         f"— raw output:\n{json.dumps(data)[:500]}")
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
