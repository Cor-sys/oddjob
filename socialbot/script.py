"""Turn a Topic (+ optional research Dossier) into a mini-documentary script.

v2: the script is written like a tiny documentary — a cold-open hook, escalating
reveals, and a payoff that loops back to the start — and it emits a per-beat
*shot list* (each narration beat paired with a specific visual query) so footage
can be cut to the narration instead of stretched across random clips.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import costs
from .config import settings
from .llm import json_call
from .trends import Topic

if TYPE_CHECKING:
    from .research import Dossier

_SYSTEM = (
    "You are a short-form documentary scriptwriter (YouTube Shorts) known for "
    "openings that stop the scroll and narration that sounds like a real person, "
    "not a press release. Rules you never break:\n"
    "- The FIRST SENTENCE is the whole game: ~50% of viewers swipe away in the "
    "first 3 seconds. Lead with the single most surprising, concrete fact — no "
    "setup, no throat-clearing, no scene-setting. Never open with 'In this "
    "video', 'Today we', 'Imagine', or the topic title.\n"
    "- BE SPECIFIC, NOT GENERIC. This is the difference between a great script and "
    "a boring one. Use the real numbers, names, dates, and measurements from the "
    "facts — concrete detail is what makes it feel real. Banned: vague scale-words "
    "like 'colossal', 'massive', 'incredible', 'mysterious', 'fascinating' — "
    "instead state the actual figure or image (not 'a colossal storm' but 'a storm "
    "wider than Earth').\n"
    "- Every sentence delivers a NEW concrete fact and escalates. Never restate, "
    "never pad. Banned empty connective filler: 'but here's the thing', 'what's "
    "even crazier', 'scientists were stunned', 'little did they know'.\n"
    "- Write for the ear, like one sharp person telling you something they can't "
    "believe is true — specific and confident, never a neutral encyclopedia read.\n"
    "- Write a LOOP: the last line lands the point AND flows back into the hook "
    "so a replay feels seamless. No 'thanks for watching' / 'follow for more'.\n"
    "- No hype words ('insane', 'you won't believe', 'mind-blowing'), no clickbait, "
    "no clichés, no emoji.\n"
    "- Stay strictly within the provided facts. Never invent numbers, names, or "
    "claims. If unsure, leave it out."
)


@dataclass
class Beat:
    """One shot: the narration segment it covers + a specific visual query."""
    text: str
    query: str
    kind: str = "auto"  # auto | space | entity | stock — hint for the footage router

    def to_dict(self) -> dict:
        return {"text": self.text, "query": self.query, "kind": self.kind}

    @classmethod
    def from_dict(cls, d: dict) -> "Beat":
        d = d or {}
        return cls(
            text=str(d.get("text", "")).strip(),
            query=str(d.get("query", "")).strip(),
            kind=str(d.get("kind", "auto")).strip() or "auto",
        )


@dataclass
class Script:
    topic_title: str
    on_screen_title: str
    narration: str                                          # full voiceover text (TTS reads this)
    description: str                                        # post caption/description
    hook_text: str = ""                                     # punchy <=7-word overlay of the hook
    hashtags: list[str] = field(default_factory=list)
    broll_keywords: list[str] = field(default_factory=list)  # union of beat queries (router back-compat)
    shot_list: list[Beat] = field(default_factory=list)      # ordered beats covering the whole narration
    hook_candidates: list[str] = field(default_factory=list)  # alternative opening lines (best first)

    def to_dict(self) -> dict:
        return {
            "topic_title": self.topic_title,
            "on_screen_title": self.on_screen_title,
            "narration": self.narration,
            "description": self.description,
            "hook_text": self.hook_text,
            "hashtags": self.hashtags,
            "broll_keywords": self.broll_keywords,
            "shot_list": [b.to_dict() for b in self.shot_list],
            "hook_candidates": self.hook_candidates,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Script":
        d = d or {}
        return cls(
            topic_title=str(d.get("topic_title", "")),
            on_screen_title=str(d.get("on_screen_title", "")),
            narration=str(d.get("narration", "")),
            description=str(d.get("description", "")),
            hook_text=str(d.get("hook_text", "")),
            hashtags=[str(h) for h in d.get("hashtags", [])],
            broll_keywords=[str(k) for k in d.get("broll_keywords", [])],
            shot_list=[Beat.from_dict(b) for b in d.get("shot_list", [])],
            hook_candidates=[str(h) for h in d.get("hook_candidates", [])],
        )


def _facts_block(topic: Topic, dossier: "Dossier | None") -> tuple[str, str]:
    """Return (facts_block, filmable_subjects_hint). Uses the researched dossier
    when it has real material, else falls back to the topic summary."""
    if dossier is not None and not dossier.is_thin:
        block = "VERIFIED FACTS (do not go beyond these):\n" + "\n".join(f"- {f}" for f in dossier.facts)
        if dossier.surprising_angle:
            block += f"\n\nMOST SURPRISING TRUE ANGLE: {dossier.surprising_angle}"
        if dossier.specifics:
            block += "\n\nCONCRETE SPECIFICS: " + "; ".join(dossier.specifics)
        return block, ", ".join(dossier.entities)
    return f"FACTS (do not go beyond these): {topic.summary}", ", ".join(topic.keywords)


def _strategy_block() -> str:
    """A directives block synthesized from real analytics (Phase 5). Read-only,
    no LLM call. Empty when no strategy has been learned yet."""
    try:
        from .analytics import load_strategy
        strategy = load_strategy()
    except Exception:
        return ""
    directives = strategy.get("directives") or []
    if not directives:
        return ""
    lines = "\n".join(f"  - {d}" for d in directives[:6])
    return ("\nSTRATEGY (learned from this channel's own analytics — follow it "
            "UNLESS it would conflict with the facts above):\n" + lines + "\n")


def write_script(topic: Topic, seconds: int | None = None, dossier: "Dossier | None" = None) -> Script:
    seconds = seconds or settings.clip_seconds
    # edge-tts narrates at ~2.2 spoken words/sec in practice (measured); a higher
    # estimate makes scripts overrun the target length.
    target_words = int(seconds * 2.2)
    facts_block, subjects = _facts_block(topic, dossier)
    subjects_line = f"FILMABLE SUBJECTS: {subjects}\n" if subjects else ""
    strategy_block = _strategy_block()

    prompt = f"""Write a {seconds}-second vertical short-form MINI-DOCUMENTARY script.

NARRATOR PERSONALITY: {settings.content_tone}

TOPIC: {topic.title}
{facts_block}
{subjects_line}{strategy_block}
Structure it like a tiny documentary, not a news read:
  - COLD OPEN: the first sentence is the single most surprising, concrete fact —
    it must stop the scroll in ~3 seconds. No setup, no restating the title.
  - ESCALATE: each sentence raises the stakes or deepens the mystery, building on
    the last. One clear idea at a time.
  - PAYOFF + LOOP: the final line lands the point AND flows back into the hook so
    a replay feels seamless.

Return ONLY a JSON object with keys:
  - "narration": ~{target_words} words of spoken voiceover; only the spoken words
    (no stage directions, no "[music]").
  - "hook_candidates": 3 alternative opening lines, each a complete scroll-stopping
    first sentence built on the most surprising fact. Put your best one first.
  - "hook_text": <= 7 words, the punchiest on-screen overlay of the hook (most
    viewers watch muted). No period needed.
  - "on_screen_title": <= 6 words, a curiosity-driving title card for the open.
  - "description": 1-2 sentence caption that teases without spoiling.
  - "hashtags": 3-5 relevant, specific hashtags WITHOUT the # symbol (no #viral,
    #fyp, or other generic spam tags).
  - "shot_list": an ORDERED list of beats that together cover the WHOLE narration.
    Each beat: {{"text": "<the exact narration segment this shot covers>",
                 "query": "<a SHORT visual search phrase: 2-5 words, ONE concrete filmable subject>",
                 "kind": "space" | "entity" | "stock"}}.
    The concatenation of every beat "text" must equal the narration.
    QUERY RULES (critical — this is what fetches the footage):
      * 2-5 words naming ONE subject. NEVER a sentence, NEVER a comma-separated list.
      * Pick subjects that free stock/NASA/Wikimedia libraries actually have. If the
        exact subject is too niche to have footage (a specific unreleased vehicle,
        an internal codename), use its filmable GENERIC instead (e.g. "rocket
        launch", "Earth from orbit", "data center", "ocean splashdown").
      * Never generic filler ("news", "technology", "background").
    KIND (controls which library is searched):
      * "entity" = a specific NAMED person, place, company, craft, or mission
        (SpaceX, Starship, Voyager, JWST, NASA, a named scientist) -> real photos.
      * "space"  = generic astronomy with NO specific named craft (planets,
        galaxies, nebulae, stars, the Sun, Earth from space).
      * "stock"  = everyday/atmospheric footage (labs, crowds, cities, nature)."""

    with costs.track(stage="script"):
        data = json_call(prompt, system=_SYSTEM)
    return _script_from_data(topic, data)


def revise_script(topic: Topic, script: "Script", fc, *, dossier: "Dossier | None" = None,
                  seconds: int | None = None) -> "Script":
    """Rewrite a script to fix what the fact-check flagged — WITHOUT lowering the bar.

    Correct contradicted claims to match the verified facts, and DELETE claims that
    stay unverifiable. Never invents anything new; only fixes or cuts the flagged
    claims. One Flash call; returns a new Script to be re-vetted. This is the salvage
    pass behind `factcheck.vet_and_revise`.
    """
    seconds = seconds or settings.clip_seconds
    target_words = int(seconds * 2.2)
    facts_block, _ = _facts_block(topic, dossier)
    flagged = [c for c in getattr(fc, "claims", []) if (c.status or "").lower() != "supported"]
    if flagged:
        findings = "\n".join(
            f'  - [{c.status}] "{c.claim}"' + (f" — {c.note}" if c.note else "")
            for c in flagged
        )
    else:
        findings = f"  - {getattr(fc, 'summary', '') or 'one or more claims could not be verified'}"

    prompt = f"""A fact-checker flagged problems in this short-video narration. Rewrite it to be fully accurate — fix or cut ONLY what was flagged, keep the rest.

ORIGINAL NARRATION:
\"\"\"{script.narration}\"\"\"

{facts_block}

FACT-CHECK FINDINGS — resolve every one:
{findings}

How to resolve each:
  - CONTRADICTED / false: correct it to match the VERIFIED FACTS above. If it can't
    be made accurate from those facts, DELETE that sentence entirely.
  - UNVERIFIED / partially supported: DELETE that claim — keep nothing you can't
    support, and do NOT soften it with "reportedly"/"allegedly". Cut it.
  - Leave every OTHER sentence essentially as-is. Introduce NO new claim, number, or name.
  - Preserve the style: a cold-open hook, escalating reveals, and a final line that
    loops back to the hook. Aim for ~{target_words} words (shorter is fine after cuts).

Return ONLY a JSON object with the SAME keys as a normal script: "narration",
"hook_candidates" (3, best first), "hook_text" (<=7 words), "on_screen_title"
(<=6 words), "description", "hashtags" (3-5, no #), and "shot_list" (ordered beats
whose "text" values concatenate to the new narration; each {{"text","query","kind"}}
with query = 2-5 words naming ONE filmable subject and kind = "space"|"entity"|"stock")."""

    with costs.track(stage="revise"):
        data = json_call(prompt, system=_SYSTEM)
    return _script_from_data(topic, data)


def _clean_query(q: str) -> str:
    """Reduce a beat query to ONE short search phrase: drop everything after the
    first comma/semicolon and cap at ~6 words. Long multi-clause queries return
    junk from image search; a tight phrase matches far better."""
    first = re.split(r"[,;]", q or "")[0].strip()
    words = first.split()
    return " ".join(words[:6]) if words else first


def _script_from_data(topic: Topic, data: object) -> Script:
    data = data if isinstance(data, dict) else {}
    narration = str(data.get("narration", "")).strip()
    hooks = [str(h).strip() for h in data.get("hook_candidates", []) if str(h).strip()]
    beats = [
        Beat.from_dict(b)
        for b in data.get("shot_list", [])
        if isinstance(b, dict) and str(b.get("query", "")).strip()
    ]
    # Tighten any over-written query (e.g. a 4-clause sentence) to a searchable phrase.
    for b in beats:
        b.query = _clean_query(b.query) or b.query
    # Router back-compat: broll_keywords = the beat queries (fallback to anything
    # the model or topic gave us so footage fetch never starves).
    keywords = (
        [b.query for b in beats]
        or [str(k).strip() for k in data.get("broll_keywords", []) if str(k).strip()]
        or list(topic.keywords)
    )
    # Safety net: if the model returned no usable shot list, split the narration
    # into sentences and pair each with a keyword so footage still tracks the script.
    if not beats and narration:
        beats = _fallback_beats(narration, keywords)
    return Script(
        topic_title=topic.title,
        on_screen_title=str(data.get("on_screen_title", topic.title)).strip(),
        narration=narration,
        description=str(data.get("description", "")).strip(),
        hook_text=str(data.get("hook_text", "")).strip(),
        hashtags=[str(h).lstrip("#").strip() for h in data.get("hashtags", []) if h][:5],
        broll_keywords=keywords,
        shot_list=beats,
        hook_candidates=hooks,
    )


def _fallback_beats(narration: str, keywords: list[str]) -> list[Beat]:
    """Sentence-aligned beats used only when the model omits a usable shot list."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", narration) if s.strip()]
    if not sentences:
        return []
    kw = keywords or ["documentary background"]
    return [Beat(text=s, query=kw[i % len(kw)], kind="stock") for i, s in enumerate(sentences)]
