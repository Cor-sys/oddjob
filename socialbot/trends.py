"""Discover trending topics using Gemini + Google Search grounding."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import settings
from .llm import grounded_json

_SYSTEM = (
    "You are a sharp news/trends researcher for a short-form video channel that "
    "prides itself on NOT being generic. You surface stories that are genuinely "
    "current AND have a specific, surprising, or under-told angle — the detail "
    "most channels miss — rather than the one bland headline everyone is already "
    "running. Every topic must be real and verifiable; no rumors, no speculation. "
    "Each must have a concrete visual hook (a place, object, scene) that footage "
    "could show."
)


@dataclass
class Topic:
    title: str
    summary: str
    why_trending: str = ""
    keywords: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "summary": self.summary,
            "why_trending": self.why_trending,
            "keywords": self.keywords,
            "sources": self.sources,
        }


def discover(count: int = 5, niche: str | None = None) -> list[Topic]:
    """Return up to `count` trending topics, optionally biased to a niche."""
    niche = (niche if niche is not None else settings.content_niche).strip()
    focus = f"Focus on this niche/area: {niche}." if niche else "General trending news."

    prompt = f"""Using up-to-date web search, find {count} stories worth a short video,
trending in the last 24-48 hours. {focus}

Selection rules:
  - Favor a SPECIFIC angle over a broad headline (e.g. not "AI is booming" but the
    one concrete development that just happened).
  - Mix it up — don't return {count} variations of the same story.
  - Prefer stories with a clear, filmable visual; skip abstract/opinion pieces.
  - Must be factual and currently verifiable via the search results.

For each story return an object with:
  - "title": a curiosity-driving short title (max ~8 words), no clickbait lies
  - "summary": 2-3 factual sentences with the concrete details that make it interesting
  - "why_trending": one sentence on why it's hot right now
  - "keywords": 4-6 SPECIFIC visual b-roll search terms (concrete nouns/scenes,
    not generic words like "news" or "technology")

Return ONLY a JSON array of these objects. No prose, no markdown."""

    data, sources = grounded_json(prompt, system=_SYSTEM)
    if isinstance(data, dict):
        data = data.get("topics") or next(
            (v for v in data.values() if isinstance(v, list)), []
        )

    topics: list[Topic] = []
    for item in data or []:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        topics.append(
            Topic(
                title=str(item.get("title", "")).strip(),
                summary=str(item.get("summary", "")).strip(),
                why_trending=str(item.get("why_trending", "")).strip(),
                keywords=[str(k).strip() for k in item.get("keywords", []) if k],
                sources=sources,
            )
        )
    return topics[:count]
