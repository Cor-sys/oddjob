"""Coverage memory: don't let the channel make the same story twice.

The source of truth for "what we've covered" is the artifacts themselves — the
meta.json in data/published/, data/pending/ and data/reserve/. `data/used_topics
.json` is a *derived cache* (a rolling window of story-keys) that survives across
runs and machines, but it is NOT authoritative: every dedup read UNIONs the cache
with a fresh scan of those folders, so if the cache is ever wiped, stale, or out
of sync, the scan reconstitutes coverage from reality. (A `git restore data/` once
wiped the cache and the engine re-made a story it had already published — this
self-heal closes that whole class of bug.)

Identity is by STORY, not title. A fingerprint of the title alone misses the same
event reworded across days ("ISS Air Leak Worsens" vs "Russia's Risky Saw Repair"),
so the dedup key folds in the topic's keywords and matches at a lower Jaccard
threshold (see _STORY_SIMILARITY). The plain title fingerprint (_fingerprint) is
kept unchanged for the topic_bank / demand callers that depend on it.

This module reads meta.json directly (via config dir constants) and never imports
`review`/`publish`, so those layers can import this one to record coverage at
publish time without a cycle.
"""
from __future__ import annotations

import json
import re

from .config import DATA_DIR, PENDING_DIR, PUBLISHED_DIR, RESERVE_DIR

_USED_FILE = DATA_DIR / "used_topics.json"

# How many recent story-keys to keep in the derived cache (rolling window).
_KEEP = 80
# Title-only match threshold (kept for topic_bank, which fingerprints titles).
_SIMILARITY = 0.5
# Story-key match threshold. The key folds in keywords, so the same event reworded
# overlaps more. Measured Jaccard on real data: reworded ISS pair = 0.375 (caught);
# distinct space topics — Jupiter/Saturn = 0.125, Voyager/Artemis = 0.056,
# black-hole/neutron-star = 0.0 (all safe). 0.3 sits in that gap with margin —
# don't raise it blindly.
_STORY_SIMILARITY = 0.3

# review status values, inlined to avoid importing `review` (cycle-free).
_REJECTED = "rejected"
_RESERVE = "reserve"

# Common words that carry no topic signal — excluded from the fingerprint.
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at",
    "by", "from", "as", "is", "are", "was", "were", "be", "this", "that", "it",
    "its", "new", "now", "after", "over", "into", "amid", "via", "could", "will",
    "may", "first", "report", "reports", "says", "say",
}

# Memoized result of the artifact walk — one disk pass per process. Reset whenever
# we record new coverage so a later read in the same process sees it.
_walk_cache: list[tuple[str, dict]] | None = None


def _fingerprint(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return {w for w in words if w not in _STOP and len(w) > 2}


def _similar(a: set[str], b: set[str], *, threshold: float = _SIMILARITY) -> bool:
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= threshold


def _key_from_parts(title: str, keywords=None) -> set[str]:
    """Story-key: the title fingerprint plus each keyword's fingerprint. Folding in
    keywords is what lets the same event reworded across days still collide."""
    key = _fingerprint(title or "")
    for kw in (keywords or []):
        key |= _fingerprint(str(kw))
    return key


def _topic_key(topic) -> set[str]:
    """Story-key for a Topic-like object (uses .title + .keywords)."""
    return _key_from_parts(getattr(topic, "title", ""), getattr(topic, "keywords", None))


def _meta_key(meta: dict) -> set[str]:
    """Story-key for an artifact's meta.json (topic_title + topic.keywords).
    Tolerates promo items that carry only a title."""
    topic = meta.get("topic") or {}
    title = meta.get("topic_title") or topic.get("title", "")
    return _key_from_parts(title, topic.get("keywords"))


# ── artifact scan (the authoritative source of truth) ─────────────────────────

def _disk_walk() -> list[tuple[str, dict]]:
    """Read every <id>/meta.json under the published/pending/reserve queues.
    Returns (dir_name, meta) pairs. Pure read; tolerates missing/partial files."""
    items: list[tuple[str, dict]] = []
    for base in (PUBLISHED_DIR, PENDING_DIR, RESERVE_DIR):
        try:
            children = list(base.iterdir())
        except (OSError, FileNotFoundError):
            continue
        for d in children:
            if not d.is_dir():
                continue
            try:
                meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(meta, dict):
                items.append((d.name, meta))
    return items


def _walk() -> list[tuple[str, dict]]:
    """Memoized `_disk_walk` — one disk pass per process."""
    global _walk_cache
    if _walk_cache is None:
        _walk_cache = _disk_walk()
    return _walk_cache


def _reset_scan_cache() -> None:
    global _walk_cache
    _walk_cache = None


def _scan_coverage() -> list[set[str]]:
    """Story-keys for every covered artifact (published/pending/reserve), skipping
    fact-check REJECTED items so a debunked story can still be covered later."""
    keys: list[set[str]] = []
    for _name, meta in _walk():
        if meta.get("status") == _REJECTED:
            continue
        key = _meta_key(meta)
        if key:
            keys.append(key)
    return keys


def _coverage() -> list[set[str]]:
    """The unified read every gate consults: the live artifact scan UNIONed with
    the derived cache. Cache drift/wipe is harmless — the scan rebuilds coverage."""
    return _scan_coverage() + [set(fp) for fp in _load()]


# ── derived cache (rolling window, survives across runs/machines) ─────────────

def _load() -> list[list[str]]:
    if _USED_FILE.exists():
        try:
            data = json.loads(_USED_FILE.read_text(encoding="utf-8"))
            return [list(fp) for fp in data if isinstance(fp, list)]
        except (ValueError, OSError):
            return []
    return []


def _save(keys: list[set[str]]) -> None:
    try:
        _USED_FILE.write_text(
            json.dumps([sorted(fp) for fp in keys[-_KEEP:]]),
            encoding="utf-8",
        )
    except OSError:
        pass


# ── public API ────────────────────────────────────────────────────────────────

def filter_new(topics: list) -> list:
    """Drop topics that match a covered story (anywhere in published/pending/reserve
    or the cache) OR an earlier topic in this same batch. Returns survivors in their
    original order; does NOT record them (coverage is recorded at publish time, or
    call remember() explicitly)."""
    seen = list(_coverage())
    fresh = []
    for topic in topics:
        key = _topic_key(topic)
        if not key:
            fresh.append(topic)  # nothing to match on — let it through
            continue
        if any(_similar(key, prev, threshold=_STORY_SIMILARITY) for prev in seen):
            print(f"  [dedup] skipping recent/duplicate topic: {topic.title}")
            continue
        seen.append(key)          # also dedupe within this batch
        fresh.append(topic)
    return fresh


def is_covered_meta(meta: dict) -> bool:
    """True if this story has already AIRED or is QUEUED to air (a published or
    pending artifact). Used by the reserve path (don't re-air a story a recent batch
    already posted) and the custom path (warn-only).

    Deliberately checks only published/pending — NOT other un-aired reserve recipes
    (so a recipe never matches itself, since it lives in reserve/) and NOT the rolling
    cache (which may hold the recipe's own banked key)."""
    key = _meta_key(meta)
    if not key:
        return False
    return any(
        _similar(key, _meta_key(m), threshold=_STORY_SIMILARITY)
        for _name, m in _walk()
        if m.get("status") not in (_RESERVE, _REJECTED)
    )


def recent_titles(n: int = 30) -> list[str]:
    """Up to `n` recently-covered human titles (newest first), for the discover
    avoid-block. Includes REJECTED items so the model also steers clear of stories
    we evaluated and dropped. Folder names are timestamp-prefixed, so reverse-
    sorting them yields recency for free."""
    titles: list[str] = []
    seen_t: set[str] = set()
    for _name, meta in sorted(_walk(), key=lambda it: it[0], reverse=True):
        topic = meta.get("topic") or {}
        title = (meta.get("topic_title") or topic.get("title") or "").strip()
        low = title.lower()
        if title and low not in seen_t:
            seen_t.add(low)
            titles.append(title)
        if len(titles) >= n:
            break
    return titles


def remember(topics: list) -> None:
    """Record topics as covered in the derived cache so future runs won't repeat
    them even before they leave a scannable artifact."""
    if not topics:
        return
    seen = [set(fp) for fp in _load()]
    for topic in topics:
        key = _topic_key(topic)
        if key:
            seen.append(key)
    _save(seen)
    _reset_scan_cache()


def record_meta(meta: dict) -> None:
    """Record one aired artifact's story in the cache. Called at publish time so the
    record is durable even where the published/ folder won't persist (e.g. CI)."""
    key = _meta_key(meta)
    if not key:
        return
    seen = [set(fp) for fp in _load()]
    seen.append(key)
    _save(seen)
    _reset_scan_cache()


def reconcile() -> dict:
    """Fold every covered artifact's story-key into the cache (de-duped), so the
    cache reflects everything actually covered. Idempotent; cheap (one memoized
    walk). Run once per process near the start of a batch."""
    existing = [set(fp) for fp in _load()]
    have = {frozenset(s) for s in existing}
    merged = list(existing)
    scanned = 0
    for key in _scan_coverage():
        scanned += 1
        fk = frozenset(key)
        if fk and fk not in have:
            have.add(fk)
            merged.append(key)
    _save(merged)
    _reset_scan_cache()
    return {"scanned": scanned, "cache_size": len(_load())}
