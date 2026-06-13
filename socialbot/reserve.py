"""Reserve bank: tournament runners-up, stored as re-renderable *recipes*.

The daily batch posts the best `posts_per_day` videos and banks the remaining
survivors here — not as finished mp4s, but as the full recipe (topic, research,
script + shot list, fact-check, scores) needed to re-render on demand with ZERO
new LLM calls. On a thin day the batch tops up its post slots from this bank.

A banked recipe stays valid only so long as its facts do; a recipe older than
`reserve_revet_days` is re-fact-checked (one grounded call, off the daily batch
budget) before it can be rendered + scheduled, so we never publish stale claims.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import review
from .config import RESERVE_DIR, settings

# Re-exported for callers that want the status constant from here.
RESERVE = review.RESERVE


def _score(item: review.Item) -> float:
    try:
        return float(item.meta.get("judge_score", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _created(item: review.Item) -> datetime:
    raw = item.meta.get("created_at", "")
    try:
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def bank(meta: dict) -> review.Item:
    """Bank one finalist recipe. `meta` should already carry status=RESERVE and
    the full recipe (topic/research/script/factcheck/judge_score); no clip key."""
    meta = dict(meta)
    meta["status"] = review.RESERVE
    meta.pop("clip", None)  # recipes are re-rendered; never carry a stale clip
    item = review.create(meta, base=RESERVE_DIR)
    print(f"  -> banked recipe {item.id} (score {_score(item):.0f})")
    return item


def list_reserve() -> list[review.Item]:
    """All banked recipes, best (highest judge score) first."""
    items = review.list_items(base=RESERVE_DIR)
    return sorted(items, key=_score, reverse=True)


def best(n: int, *, exclude: set[str] | None = None) -> list[review.Item]:
    """The `n` highest-scoring recipes, skipping any id in `exclude`."""
    exclude = exclude or set()
    return [it for it in list_reserve() if it.id not in exclude][:max(0, n)]


def _is_stale(item: review.Item) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.reserve_revet_days)
    return _created(item) < cutoff


def _revet(item: review.Item) -> str:
    """Re-fact-check a recipe (one grounded call). Updates meta['factcheck'] and
    returns the new verdict. Used before rendering a recipe older than the
    re-vet window so we never schedule stale claims."""
    from . import factcheck
    from .script import Script

    script = Script.from_dict(item.meta.get("script", {}))
    fc = factcheck.vet(script)
    item.meta["factcheck"] = fc.to_dict()
    item.meta["revetted_at"] = datetime.now(timezone.utc).isoformat()
    item.save()
    print(f"  -> re-vetted {item.id}: {fc.verdict}")
    return fc.verdict


def render_reserve(item_id: str, *, publish_at: str | None = None, force: bool = False) -> review.Item:
    """Rehydrate a banked recipe and render its clip (0 LLM calls). If the recipe
    is older than the re-vet window it is re-fact-checked first. When `publish_at`
    is given the clip is scheduled (native publishAt) — but only if it clears the
    publish gate, unless `force` is set."""
    from . import factcheck, pipeline
    from .script import Script
    from .trends import Topic

    item = review.get(item_id)
    if not item:
        raise KeyError(item_id)
    if item.status != review.RESERVE:
        raise RuntimeError(f"{item_id} is '{item.status}', not a reserve recipe.")

    if not force and _is_stale(item):
        verdict = _revet(item)
        if verdict == factcheck.REJECTED:
            review.reject(item_id, reason="reserve re-vet: facts no longer hold")
            raise RuntimeError(f"{item_id} failed re-vet ({verdict}); not rendering.")

    script = Script.from_dict(item.meta.get("script", {}))
    topic = Topic(**{k: item.meta.get("topic", {}).get(k)
                     for k in ("title", "summary", "why_trending", "keywords", "sources")
                     if k in item.meta.get("topic", {})})
    print(f"  -> rendering reserve recipe {item_id}")
    pipeline._render(item, script, topic)
    item.save()

    if publish_at:
        if not force and not pipeline._publishable_verdict(item.meta):
            verdict = item.meta.get("factcheck", {}).get("verdict")
            print(f"  -> NOT scheduling {item_id}: verdict={verdict} (needs manual review)")
            return item
        # A recipe was banked before airing; a later batch may have since covered
        # the same story. Don't re-air a duplicate (unless forced).
        from . import topic_history
        if not force and topic_history.is_covered_meta(item.meta):
            print(f"  -> NOT scheduling {item_id}: story already covered recently")
            return item
        pipeline.schedule_item(item, publish_at)
    return item


def prune_reserve(keep: int | None = None) -> int:
    """Keep only the top `keep` recipes by score; delete the rest. Returns the
    number removed."""
    import shutil

    keep = settings.reserve_max if keep is None else keep
    ranked = list_reserve()
    drop = ranked[max(0, keep):]
    for it in drop:
        try:
            shutil.rmtree(it.dir)
        except OSError:
            pass
    if drop:
        print(f"  -> pruned {len(drop)} recipe(s); kept {min(keep, len(ranked))}")
    return len(drop)
