"""Free demand signal: validate concepts against real YouTube search interest.

We never *originate* topics from autocomplete (that would drift off-niche fast).
Instead, for concepts we already have, we ask YouTube's public autocomplete
endpoint what people actually type around each concept's own keywords. That gives:
  - a rough `demand` score (how much live search interest the angle has), and
  - the real `phrasings` searchers use (handy for titles/hooks).

Then ONE Flash-Lite call filters the pool down to what's genuinely on-niche, so
a stray autocomplete result can never pull the channel off-topic. The HTTP itself
costs nothing and degrades gracefully: if the endpoint is unreachable, demand
stays 0 and the concept still flows through.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from . import costs
from .config import settings
from .llm import json_call
from .topic_history import _fingerprint
from .trends import Topic

# Public, unofficial autocomplete endpoint. client=firefox returns clean JSON:
#   ["<query>", ["suggestion 1", "suggestion 2", ...]]
_AC_URL = "https://suggestqueries.google.com/complete/search"
_TIMEOUT = 4.0
_UA = "Mozilla/5.0 (compatible; OddjobBot/1.0)"

_NICHE_SYSTEM = (
    "You are a strict content gatekeeper for a single-niche short-form channel. "
    "You decide only whether each candidate clearly belongs in the channel's "
    "niche. You reply with ONLY the requested JSON — never prose."
)


def suggest(seed: str) -> list[str]:
    """Return YouTube autocomplete suggestions for `seed` (best-effort, [] on error)."""
    seed = (seed or "").strip()
    if not seed:
        return []
    qs = urllib.parse.urlencode({"client": "firefox", "ds": "yt", "q": seed})
    req = urllib.request.Request(f"{_AC_URL}?{qs}", headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
        data = json.loads(raw)
        if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list):
            return [str(s).strip() for s in data[1] if str(s).strip()]
    except Exception:
        pass  # endpoint blocked / changed / offline — demand just stays unknown
    return []


def _seeds(concept: Topic) -> list[str]:
    """Intent-anchored seeds drawn from the concept's OWN terms, so suggestions
    stay on-topic (we never seed from unrelated trending queries)."""
    seeds: list[str] = []
    for kw in concept.keywords:
        kw = kw.strip()
        if kw and kw.lower() not in (s.lower() for s in seeds):
            seeds.append(kw)
    if not seeds and concept.title:
        seeds.append(concept.title)
    return seeds[:3]


def _demand_score(concept: Topic, suggestions: list[str]) -> float:
    """A 0-100 proxy: how many real autocomplete phrasings overlap the concept's
    own vocabulary, weighted up by sheer suggestion volume."""
    if not suggestions:
        return 0.0
    vocab = _fingerprint(concept.title) | {w for kw in concept.keywords for w in _fingerprint(kw)}
    overlap = sum(1 for s in suggestions if _fingerprint(s) & vocab)
    return min(100.0, overlap * 8.0 + len(suggestions) * 2.0)


def _pick_phrasings(concept: Topic, suggestions: list[str], n: int = 5) -> list[str]:
    """Keep the autocomplete phrasings that actually relate to the concept."""
    vocab = _fingerprint(concept.title) | {w for kw in concept.keywords for w in _fingerprint(kw)}
    related = [s for s in suggestions if _fingerprint(s) & vocab] or suggestions
    return list(dict.fromkeys(related))[:n]


def _niche_filter(concepts: list[Topic], niche_def: str) -> list[Topic]:
    """ONE Flash-Lite call: drop any concept that isn't clearly on-niche. On any
    failure, keep the whole pool (the discover step already biases to-niche)."""
    listing = "\n".join(f"{i}. {c.title} — {c.summary}" for i, c in enumerate(concepts, 1))
    prompt = f"""The channel's niche is: {niche_def}

Decide, for each candidate, whether it CLEARLY belongs in that niche. Be strict:
drop anything off-topic, generic, or only loosely related.

CANDIDATES:
{listing}

Return ONLY JSON covering EVERY candidate:
{{"verdicts":[{{"index":<1-based number above>,"on_niche":true|false}}]}}"""
    try:
        with costs.track(stage="demand"):
            data = json_call(prompt, system=_NICHE_SYSTEM, model=settings.gemini_calls_model)
    except Exception as e:
        print(f"  [demand] niche filter unavailable ({type(e).__name__}); keeping all")
        return concepts

    verdicts = data.get("verdicts") if isinstance(data, dict) else None
    if not isinstance(verdicts, list):
        return concepts
    drop: set[int] = set()
    for v in verdicts:
        if isinstance(v, dict) and v.get("on_niche") is False:
            try:
                drop.add(int(v.get("index")) - 1)
            except (TypeError, ValueError):
                pass
    kept = [c for i, c in enumerate(concepts) if i not in drop]
    if len(kept) < len(concepts):
        print(f"  [demand] niche filter dropped {len(concepts) - len(kept)} off-niche concept(s)")
    return kept or concepts  # never return an empty pool


def enrich(concepts: list[Topic], niche_def: str | None = None) -> list[Topic]:
    """Attach a demand score + real search phrasings to each concept (free HTTP),
    then niche-filter the pool (1 Flash-Lite call). Returns the on-niche concepts."""
    if not concepts:
        return []
    niche_def = niche_def if niche_def is not None else settings.content_niche
    for c in concepts:
        suggestions: list[str] = []
        for s in _seeds(c):
            suggestions.extend(suggest(s))
        suggestions = list(dict.fromkeys(suggestions))
        c.demand = _demand_score(c, suggestions)
        c.phrasings = _pick_phrasings(c, suggestions)
    return _niche_filter(concepts, niche_def)
