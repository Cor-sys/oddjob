"""Tag every video with an "experiment arm" so analytics can tell us what works.

Retention data is only useful if we can attribute it: was it the topic cluster,
the hook style, the length, or the voice that made a video over- or under-perform?
So at render time we stamp each video with a small, fully-deterministic arm —
{topic_cluster, hook_style, length_bucket, voice} — and we deliberately jitter
the length across videos so "length" becomes a real, comparable variable rather
than a constant. No LLM calls: this is pure classification.
"""
from __future__ import annotations

import random
import re

from .config import settings

# Niche clusters, checked in this order (UFO/UAP before space, since "alien"
# should land in ufo_uap, not space).
_CLUSTERS: list[tuple[str, set[str]]] = [
    ("ufo_uap", {"ufo", "ufos", "uap", "uaps", "alien", "aliens", "extraterrestrial",
                 "abduction", "roswell", "flying", "saucer", "paranormal", "cryptid"}),
    ("space", {"space", "nasa", "esa", "planet", "planets", "star", "stars", "galaxy",
               "galaxies", "nebula", "telescope", "rocket", "rockets", "astronaut",
               "moon", "mars", "venus", "jupiter", "saturn", "asteroid", "comet",
               "cosmic", "cosmos", "spacecraft", "orbit", "interstellar", "supernova"}),
    ("ai_tech", {"ai", "llm", "model", "models", "robot", "robots", "quantum", "qubit",
                 "chip", "chips", "computer", "computing", "algorithm", "software",
                 "neural", "semiconductor", "gpu", "data", "machine"}),
]

# Length buckets (seconds) used as discrete arms.
_BUCKETS = {"short": (30, 35), "mid": (36, 40), "long": (41, 45)}


def classify_cluster(text: str) -> str:
    words = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    for name, vocab in _CLUSTERS:
        if words & vocab:
            return name
    return "other"


def classify_hook(text: str) -> str:
    t = (text or "").strip().lower()
    if "?" in t:
        return "question"
    if re.search(r"\d", t):
        return "number"
    if re.search(r"\b(no one|nobody|never|nothing|none|can't|cannot|isn't|wasn't|don't)\b", t):
        return "negation"
    return "statement"


def length_bucket(seconds: float) -> str:
    if seconds <= 35:
        return "short"
    if seconds <= 40:
        return "mid"
    return "long"


def jittered_seconds(strategy: dict | None = None) -> int:
    """A per-video length within [CLIP_SECONDS_MIN, CLIP_SECONDS_MAX]. If a learned
    strategy favors a length bucket, bias toward that bucket; otherwise spread
    randomly across the range so length is a real experiment variable."""
    lo, hi = settings.clip_seconds_min, settings.clip_seconds_max
    favored = _favored_bucket(strategy)
    if favored and favored in _BUCKETS:
        blo, bhi = _BUCKETS[favored]
        lo, hi = max(lo, blo), min(hi, bhi)
        if lo > hi:  # config narrower than the bucket — fall back to full range
            lo, hi = settings.clip_seconds_min, settings.clip_seconds_max
    return random.randint(lo, hi)


def _favored_bucket(strategy: dict | None) -> str | None:
    weights = (strategy or {}).get("weights", {}).get("length_bucket", {})
    if not isinstance(weights, dict) or not weights:
        return None
    try:
        best = max(weights.items(), key=lambda kv: float(kv[1]))
        return best[0] if float(best[1]) > 1.0 else None
    except (TypeError, ValueError):
        return None


def assign_arm(topic, script, voice: str | None = None, seconds: float | None = None) -> dict:
    """Classify one video into its experiment arm (deterministic)."""
    cluster_text = " ".join([
        getattr(topic, "title", ""), getattr(topic, "summary", ""),
        " ".join(getattr(topic, "keywords", []) or []),
    ])
    hook = ""
    if getattr(script, "hook_candidates", None):
        hook = script.hook_candidates[0]
    hook = hook or getattr(script, "hook_text", "") or getattr(script, "narration", "")[:80]
    return {
        "topic_cluster": classify_cluster(cluster_text),
        "hook_style": classify_hook(hook),
        "length_bucket": length_bucket(seconds if seconds is not None else settings.clip_seconds),
        "voice": voice or "",
    }
