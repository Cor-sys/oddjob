"""Remember recently-covered topics so the channel doesn't post near-duplicate
stories (e.g. the same rocket explosion twice in an hour). Duplicates split
watch-time, look spammy, and waste a generation slot.

Matching is by significant-word overlap on the title (Jaccard), so small
wording changes ("Rocket Explodes" vs "Rocket Explodes During Test") still
collide. State persists in data/used_topics.json and is committed back by the
scheduled CI job, same as the footage/voice dedup.
"""
from __future__ import annotations

import json
import re

from .config import DATA_DIR

_USED_FILE = DATA_DIR / "used_topics.json"

# How many recent topic fingerprints to keep (rolling window).
_KEEP = 80
# Two titles are "the same story" at/above this word-overlap (Jaccard) ratio.
_SIMILARITY = 0.5

# Common words that carry no topic signal — excluded from the fingerprint.
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at",
    "by", "from", "as", "is", "are", "was", "were", "be", "this", "that", "it",
    "its", "new", "now", "after", "over", "into", "amid", "via", "could", "will",
    "may", "first", "report", "reports", "says", "say",
}


def _fingerprint(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return {w for w in words if w not in _STOP and len(w) > 2}


def _similar(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return False
    overlap = len(a & b)
    return overlap / len(a | b) >= _SIMILARITY


def _load() -> list[list[str]]:
    if _USED_FILE.exists():
        try:
            data = json.loads(_USED_FILE.read_text(encoding="utf-8"))
            return [list(fp) for fp in data if isinstance(fp, list)]
        except (ValueError, OSError):
            return []
    return []


def _save(fingerprints: list[set[str]]) -> None:
    try:
        _USED_FILE.write_text(
            json.dumps([sorted(fp) for fp in fingerprints[-_KEEP:]]),
            encoding="utf-8",
        )
    except OSError:
        pass


def filter_new(topics: list) -> list:
    """Drop topics that match a recently-covered story OR an earlier topic in this
    same batch. Returns the surviving topics in their original order; does NOT
    record them (call remember() once a topic is actually used)."""
    seen = [set(fp) for fp in _load()]
    fresh = []
    for topic in topics:
        fp = _fingerprint(getattr(topic, "title", ""))
        if not fp:
            fresh.append(topic)  # nothing to match on — let it through
            continue
        if any(_similar(fp, prev) for prev in seen):
            print(f"  [dedup] skipping recent/duplicate topic: {topic.title}")
            continue
        seen.append(fp)          # also dedupe within this batch
        fresh.append(topic)
    return fresh


def remember(topics: list) -> None:
    """Record topics as covered so future runs won't repeat them."""
    if not topics:
        return
    seen = [set(fp) for fp in _load()]
    for topic in topics:
        fp = _fingerprint(getattr(topic, "title", ""))
        if fp:
            seen.append(fp)
    _save(seen)
