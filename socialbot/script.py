"""Turn a Topic into a short-form video script + publishing metadata."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import costs
from .config import settings
from .llm import json_call
from .trends import Topic

_SYSTEM = (
    "You are a short-form video scriptwriter (YouTube Shorts / Facebook Reels) "
    "known for hooks that stop the scroll and writing that sounds like a real "
    "person talking, not a press release. Rules you never break:\n"
    "- The FIRST SENTENCE is the whole game: ~50% of viewers swipe away in the "
    "first 3 seconds. Lead with the single most surprising, concrete fact (or a "
    "sharp question it answers) — no setup, no throat-clearing, no scene-setting. "
    "Never open with 'In this video', 'Today we', 'Imagine', or the topic title.\n"
    "- Write a LOOP, not an essay: the last sentence should flow back into the "
    "first so a replay feels seamless. No 'thanks for watching' / 'follow for "
    "more' sign-off — end on a beat that makes the opening hit again.\n"
    "- Sound human: short sentences, natural rhythm, one clear idea at a time. "
    "Write for the ear, not the page.\n"
    "- No hype words ('insane', 'you won't believe', 'mind-blowing'), no filler, "
    "no clichés, no emoji.\n"
    "- Stay strictly within the provided facts. Never invent numbers, names, or "
    "claims. If unsure, leave it out."
)


@dataclass
class Script:
    topic_title: str
    on_screen_title: str
    narration: str           # the full voiceover text (what TTS will read)
    description: str         # post caption/description
    hook_text: str = ""      # punchy <=7-word overlay of the hook (for a text burn-in)
    hashtags: list[str] = field(default_factory=list)
    broll_keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "topic_title": self.topic_title,
            "on_screen_title": self.on_screen_title,
            "narration": self.narration,
            "description": self.description,
            "hook_text": self.hook_text,
            "hashtags": self.hashtags,
            "broll_keywords": self.broll_keywords,
        }


def write_script(topic: Topic, seconds: int | None = None) -> Script:
    seconds = seconds or settings.clip_seconds
    # ~2.6 spoken words/sec is a comfortable narration pace.
    target_words = int(seconds * 2.6)

    prompt = f"""Write a {seconds}-second vertical short-form video script.

NARRATOR PERSONALITY: {settings.content_tone}

TOPIC: {topic.title}
FACTS (do not go beyond these): {topic.summary}
WHY IT'S TRENDING: {topic.why_trending}

Requirements:
  - "narration": ~{target_words} words of spoken voiceover.
      * Line 1 must be a scroll-stopping HOOK — the single most surprising,
        concrete fact, landed in the first ~3 seconds. NOT the topic title
        restated, no setup or scene-setting before it.
      * Then deliver the key facts in a natural spoken flow.
      * The LAST line must loop back into the hook so a replay feels seamless —
        no "thanks for watching" / "follow for more" sign-off.
      * Only the words to be spoken — no stage directions, no "[music]".
  - "hook_text": <= 7 words, the punchiest possible phrasing of the hook, for a
    big on-screen text overlay (most viewers watch muted). No period needed.
  - "on_screen_title": <= 6 words, a curiosity-driving title card for the open.
  - "description": 1-2 sentence post caption that teases without spoiling.
  - "hashtags": 3-5 relevant, specific hashtags WITHOUT the # symbol (no #viral,
    #fyp, or other generic spam tags).
  - "broll_keywords": 4-6 SPECIFIC visual search terms tied to THIS story's
    concrete nouns/scenes (not generic terms like "news", "world", "technology").

Return ONLY a JSON object with keys:
  on_screen_title, hook_text, narration, description, hashtags, broll_keywords"""

    with costs.track(stage="script"):
        data = json_call(prompt, system=_SYSTEM)
    return Script(
        topic_title=topic.title,
        on_screen_title=str(data.get("on_screen_title", topic.title)).strip(),
        narration=str(data.get("narration", "")).strip(),
        description=str(data.get("description", "")).strip(),
        hook_text=str(data.get("hook_text", "")).strip(),
        hashtags=[str(h).lstrip("#").strip() for h in data.get("hashtags", []) if h][:5],
        broll_keywords=[str(k).strip() for k in data.get("broll_keywords", []) if k]
        or topic.keywords,
    )
