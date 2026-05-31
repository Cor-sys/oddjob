"""A persistent bank of scored concepts, so a great idea we didn't have room to
make today isn't thrown away — it carries forward to compete again tomorrow.

The daily tournament scores far more concepts than it can develop. The winners
get made; the rest are banked here with their score. Each new batch merges the
top unused bank concepts back into the pool, so strong-but-unused ideas keep
getting another shot until they're either made or decayed out. Dedup reuses the
same title-fingerprint logic as `topic_history`, so the bank never fills with
near-duplicates of the same story.

State lives in data/topic_bank.json (UTF-8 JSON, best-effort writes) and is
committed back by the daily CI job alongside the other dedup state.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .config import DATA_DIR, settings
from .topic_history import _fingerprint, _similar
from .trends import Topic

_BANK_FILE = DATA_DIR / "topic_bank.json"
_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(title: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:48] or "concept"


def load() -> dict:
    if _BANK_FILE.exists():
        try:
            data = json.loads(_BANK_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("concepts"), list):
                return data
        except (ValueError, OSError):
            pass
    return {"version": _VERSION, "updated_at": _now(), "concepts": []}


def save(bank: dict) -> None:
    bank["version"] = _VERSION
    bank["updated_at"] = _now()
    try:
        _BANK_FILE.write_text(json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # bookkeeping must never break the pipeline


def _topic_from_entry(e: dict) -> Topic:
    return Topic(
        title=e.get("title", ""),
        summary=e.get("summary", ""),
        why_trending=e.get("why_trending", ""),
        keywords=list(e.get("keywords", [])),
        sources=list(e.get("sources", [])),
        demand=float(e.get("demand", 0) or 0),
        phrasings=list(e.get("phrasings", [])),
    )


def _entry_from_topic(t: Topic, score: float, source: str) -> dict:
    return {
        "id": _slug(t.title),
        "title": t.title,
        "summary": t.summary,
        "why_trending": t.why_trending,
        "keywords": list(t.keywords),
        "demand": float(getattr(t, "demand", 0) or 0),
        "phrasings": list(getattr(t, "phrasings", [])),
        "score": float(score),
        "source": source,
        "first_seen": _now(),
        "last_scored": _now(),
        "used": False,
        "fingerprint": sorted(_fingerprint(t.title)),
    }


def _find_match(concepts: list[dict], fp: set[str]) -> dict | None:
    for e in concepts:
        if _similar(fp, set(e.get("fingerprint", []))):
            return e
    return None


def add_or_update(scored: list[tuple[Topic, float]], *, source: str = "batch") -> None:
    """Record freshly-scored concepts. An existing near-duplicate has its score
    refreshed (keeping the higher of old/new) and timestamp bumped; a new concept
    is added as unused so a later batch can develop it."""
    if not scored:
        return
    bank = load()
    concepts = bank["concepts"]
    for topic, score in scored:
        fp = _fingerprint(topic.title)
        if not fp:
            continue
        match = _find_match(concepts, fp)
        if match:
            match["score"] = max(float(match.get("score", 0) or 0), float(score))
            match["demand"] = max(float(match.get("demand", 0) or 0), float(getattr(topic, "demand", 0) or 0))
            match["last_scored"] = _now()
        else:
            concepts.append(_entry_from_topic(topic, score, source))
    save(bank)


def top(n: int, *, unused_only: bool = True) -> list[Topic]:
    """The `n` highest-scoring banked concepts (unused by default), best first."""
    bank = load()
    rows = [e for e in bank["concepts"] if not (unused_only and e.get("used"))]
    rows.sort(key=lambda e: float(e.get("score", 0) or 0), reverse=True)
    return [_topic_from_entry(e) for e in rows[:max(0, n)]]


def mark_used(topics: list[Topic]) -> None:
    """Flag concepts as developed so they aren't served from the bank again."""
    if not topics:
        return
    bank = load()
    fps = [(_fingerprint(t.title)) for t in topics]
    for e in bank["concepts"]:
        efp = set(e.get("fingerprint", []))
        if any(_similar(fp, efp) for fp in fps if fp):
            e["used"] = True
    save(bank)


def decay(*, factor: float = 0.85, drop_below: float = 20.0, max_concepts: int | None = None) -> dict:
    """Age the bank: drop already-used concepts, fade unused scores by `factor`,
    drop anything that falls below `drop_below`, and cap the bank to its size
    limit (highest scores kept). Returns {kept, dropped}."""
    max_concepts = settings.reserve_max if max_concepts is None else max_concepts
    bank = load()
    survivors: list[dict] = []
    for e in bank["concepts"]:
        if e.get("used"):
            continue  # already made — topic_history keeps the canonical record
        e["score"] = round(float(e.get("score", 0) or 0) * factor, 2)
        if e["score"] >= drop_below:
            survivors.append(e)
    survivors.sort(key=lambda e: float(e.get("score", 0) or 0), reverse=True)
    dropped = len(bank["concepts"]) - len(survivors[:max_concepts])
    bank["concepts"] = survivors[:max_concepts]
    save(bank)
    return {"kept": len(bank["concepts"]), "dropped": dropped}
