"""Step 1 — pick a fresh topic and write the whole script with Gemini (JSON)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict

from .gemini_client import Gemini


@dataclass
class Script:
    topic: str
    title: str
    hook: str
    sentences: list[str]
    cta: str
    description: str
    tags: list[str] = field(default_factory=list)

    @property
    def narration(self) -> list[str]:
        """Sentences actually spoken, in order."""
        out = [self.hook] + [s for s in self.sentences if s] + [self.cta]
        return [s for s in out if s and s.strip()]


def _clean(s: str) -> str:
    return re.sub(r"[*_#`]+", "", (s or "")).strip()


def _enforce_limits(script: Script, cfg: dict) -> Script:
    max_sent = int(cfg["limits"].get("max_sentences", 14))
    script.sentences = [s for s in script.sentences if s][:max_sent]
    script.title = script.title.strip()[:100]          # YouTube hard limit
    script.description = script.description.strip()[:4900]
    if cfg["upload"].get("append_shorts_hashtag"):
        script.description = (script.description + "\n\n#Shorts")[:5000]
        if "#shorts" not in script.title.lower() and len(script.title) <= 92:
            script.title += " #Shorts"
    # YouTube tags: 500 chars total — trim from the end.
    tags, total = [], 0
    for t in script.tags:
        t = _clean(t).lstrip("#")
        if not t:
            continue
        if total + len(t) + 1 > 450:
            break
        tags.append(t)
        total += len(t) + 1
    script.tags = tags
    return script


PROMPT = """You are the head writer of a viral vertical short-form video channel.
Channel niche: {niche}
Write EVERYTHING in {language}.

Task: invent ONE fresh, scroll-stopping topic in this niche that has NOT been
covered recently, then write a complete short-video script for it.

Recently used topics (avoid these and anything too similar):
{used}

Return STRICT JSON with exactly this shape:
{{
  "topic": "short name of today's topic",
  "title": "YouTube title, max 90 chars, curiosity-driven, no clickbait lies",
  "hook": "first spoken line, max 9 words, must grab instantly",
  "sentences": ["8 to 12 short punchy spoken sentences, each max 14 words"],
  "cta": "final line: follow for more, max 8 words",
  "description": "1-3 sentence YouTube description",
  "tags": ["6 to 10 search tags, single words or short phrases, no #"]
}}
{style_block}
Rules:
- Narration (hook + sentences + cta) must total 80-130 words so it fits ~40s.
- Spoken, natural style. No emojis, no stage directions, no hashtags inside
  spoken lines, no quotation marks around the lines themselves.
- Facts must be accurate; if unsure, choose a topic you are certain about.
"""


# Style profiles shape HOW the script is written. Set in config.yaml:
#   channel.style: "vox" | "facts" | "" (generic)
STYLE_PROFILES = {
    "vox": """
Channel format: VOX-STYLE EXPLAINER (visual essay / explainer journalism).
Write like smart, curious explainer journalism — precise, calm confidence, zero fluff.
Structure the narration like a Vox video:
- hook: a provocative question or bold counterintuitive claim (max 10 words)
- sentences: follow the arc  CONTEXT (how did we get here) -> COMPLICATION
  (the twist most people don't know) -> INSIGHT (the real explanation, with a
  concrete example, number, or comparison) -> SO WHAT (why it matters to the viewer)
- cta: a thoughtful closer that invites reflection or a follow, not salesy
""",
    "facts": """
Channel format: FAST FACTS (punchy listicle energy).
- hook: a shock claim or question (max 8 words)
- sentences: rapid-fire surprising facts, each self-contained, escalating wow factor
- cta: quick follow-for-more line
""",
}


def make_script(gem: Gemini, cfg: dict, topic_override: str | None, used: list[str]) -> Script:
    niche = cfg["channel"].get("niche", "").strip()
    if not niche:
        raise SystemExit("[✗] config.yaml → channel.niche is empty. Set your channel's niche first.")
    lang = cfg["channel"]["language_resolved"]
    recent = "\n".join(f"- {t}" for t in used[-40:]) or "(none yet)"

    if topic_override:
        print(f"  topic (override): {topic_override}")
    prompt = PROMPT.format(niche=niche, language=lang, used=recent)
    data = gem.json_text(prompt, temperature=1.0)

    if topic_override:
        data["topic"] = topic_override

    script = Script(
        topic=_clean(str(data.get("topic", "untitled")))[:120],
        title=_clean(str(data.get("title", data.get("topic", "Untitled")))),
        hook=_clean(str(data.get("hook", ""))),
        sentences=[_clean(str(s)) for s in (data.get("sentences") or [])],
        cta=_clean(str(data.get("cta", "Follow for more."))),
        description=_clean(str(data.get("description", ""))),
        tags=[_clean(str(t)) for t in (data.get("tags") or [])],
    )
    if len(script.narration) < 3:
        raise SystemExit(f"[✗] Script came back too short — raw model output:\n{json.dumps(data)[:500]}")
    script = _enforce_limits(script, cfg)

    words = sum(len(s.split()) for s in script.narration)
    print(f"  topic:      {script.topic}")
    print(f"  title:      {script.title}")
    print(f"  narration:  {len(script.narration)} lines, {words} words")
    return script


def save_script(script: Script, path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(script), f, ensure_ascii=False, indent=2)
