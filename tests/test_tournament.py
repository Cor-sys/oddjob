"""Tests for Phase 3: the best-of-N tournament orchestration + scheduling math.

Run with:  python -m tests.test_tournament   (or: pytest tests/test_tournament.py)

Network-free: every LLM/render/upload seam is stubbed, so run_batch is exercised
as pure orchestration logic (post/bank split, fact-check gate, score floor,
reserve shortfall fill).
"""
from __future__ import annotations

import types
from datetime import datetime, timezone


def _mk_finalist(title: str, narration: str = "A surprising fact. Then a deeper one. It loops."):
    from socialbot.research import Dossier
    from socialbot.script import Beat, Script
    from socialbot.tournament import Finalist
    from socialbot.trends import Topic

    t = Topic(title=title, summary="summary", keywords=["thing"])
    s = Script(
        topic_title=title, on_screen_title=title, narration=narration, description="d",
        hook_candidates=["A surprising fact."],
        shot_list=[Beat(text=narration, query="thing", kind="stock")],
    )
    return Finalist(topic=t, dossier=Dossier(facts=["a", "b", "c"], entities=["X"]),
                    script=s, seconds=38)


def _patch(saved: list, obj, name: str, value) -> None:
    saved.append((obj, name, getattr(obj, name)))
    setattr(obj, name, value)


def _restore(saved: list) -> None:
    for obj, name, old in reversed(saved):
        setattr(obj, name, old)


def _run_funnel(*, verdicts: dict, judges: dict, post: int):
    """Drive run_batch with stubbed stages; return (summary, scheduled, banked_titles)."""
    from socialbot import factcheck, tournament
    from socialbot.factcheck import FactCheck

    titles = list(verdicts)
    finalists = {t: _mk_finalist(t) for t in titles}
    scheduled: list[tuple[str, str]] = []
    banked_titles: list[str] = []
    saved: list = []
    try:
        topics = [finalists[t].topic for t in titles]
        _patch(saved, tournament, "concepts", lambda n=None, niche=None: topics)
        _patch(saved, tournament, "score_concepts",
               lambda ts, keep=None: [(finalists[t].topic, 75.0) for t in titles])
        _patch(saved, tournament, "develop", lambda topic, seconds=None: finalists[topic.title])
        _patch(saved, tournament, "polish", lambda f: None)
        _patch(saved, tournament, "judge",
               lambda survivors: [judges[f.topic.title] for f in survivors])
        _patch(saved, factcheck, "vet_and_revise",
               lambda script, topic, **kw: (script, FactCheck(verdict=verdicts[script.topic_title], summary="")))

        def _fake_materialize(f):
            return types.SimpleNamespace(id=f"item-{f.topic.title}", meta={})

        def _fake_bank(meta):
            title = meta["topic_title"]
            banked_titles.append(title)
            return types.SimpleNamespace(id=f"reserve-{title}")

        _patch(saved, tournament, "_materialize", _fake_materialize)
        _patch(saved, tournament.reserve, "bank", _fake_bank)
        _patch(saved, tournament.reserve, "best", lambda n, exclude=None: [])
        _patch(saved, tournament.reserve, "render_reserve",
               lambda rid, publish_at=None: scheduled.append((rid, publish_at)))
        _patch(saved, tournament.pipeline, "schedule_item",
               lambda item, slot, **kw: scheduled.append((item.id, slot)))
        _patch(saved, tournament.pipeline, "next_publish_times",
               lambda n, **kw: [f"slot-{i}" for i in range(n)])
        _patch(saved, tournament.topic_history, "remember", lambda topics: None)

        summary = tournament.run_batch(post=post)
        return summary, scheduled, banked_titles
    finally:
        _restore(saved)


def test_run_batch_posts_top_banks_rest_drops_rejected_and_below_floor():
    # A best (post), B/C above floor, D rejected (gate), C below floor (drop).
    summary, scheduled, banked = _run_funnel(
        verdicts={"A": "ok", "B": "ok", "C": "ok", "D": "rejected"},
        judges={"A": 90.0, "B": 80.0, "C": 55.0, "D": 0.0},
        post=1,
    )
    assert summary["posted"] == ["item-A"]          # only the top postable winner
    assert summary["banked"] == ["reserve-B"]       # 80 >= floor(60); not the posted one
    assert "C" not in banked                        # 55 < floor -> dropped
    assert summary["rejected"] == 1                 # D removed by the fact-check gate
    assert scheduled == [("item-A", "slot-0")]      # scheduled on the first slot


def test_run_batch_fills_shortfall_from_reserve():
    from socialbot import tournament
    # Only survivor is needs_review (non-speculative) -> NOT auto-postable, but
    # bankable; the open post slot must be filled from the reserve bank.
    saved: list = []
    scheduled: list = []
    fin = _mk_finalist("E")
    try:
        from socialbot import factcheck
        from socialbot.factcheck import FactCheck

        _patch(saved, tournament, "concepts", lambda n=None, niche=None: [fin.topic])
        _patch(saved, tournament, "score_concepts", lambda ts, keep=None: [(fin.topic, 70.0)])
        _patch(saved, tournament, "develop", lambda topic, seconds=None: fin)
        _patch(saved, tournament, "polish", lambda f: None)
        _patch(saved, tournament, "judge", lambda survivors: [70.0])
        _patch(saved, factcheck, "vet_and_revise",
               lambda script, topic, **kw: (script, FactCheck(verdict="needs_review", summary="")))
        _patch(saved, tournament, "_materialize",
               lambda f: types.SimpleNamespace(id=f"item-{f.topic.title}", meta={}))
        _patch(saved, tournament.reserve, "bank",
               lambda meta: types.SimpleNamespace(id="reserve-E"))
        _patch(saved, tournament.reserve, "best",
               lambda n, exclude=None: [types.SimpleNamespace(
                   id="recipe-1", meta={"factcheck": {"verdict": "ok"}})])
        _patch(saved, tournament.reserve, "render_reserve",
               lambda rid, publish_at=None: scheduled.append((rid, publish_at)))
        _patch(saved, tournament.pipeline, "schedule_item",
               lambda item, slot, **kw: scheduled.append((item.id, slot)))
        _patch(saved, tournament.pipeline, "next_publish_times",
               lambda n, **kw: [f"slot-{i}" for i in range(n)])
        _patch(saved, tournament.topic_history, "remember", lambda topics: None)

        summary = tournament.run_batch(post=1)
        assert summary["posted"] == ["recipe-1"]     # the shortfall was filled from reserve
        assert summary["banked"] == ["reserve-E"]    # the needs_review survivor was banked
        assert scheduled == [("recipe-1", "slot-0")]
    finally:
        _restore(saved)


def test_dry_run_renders_nothing():
    from socialbot import factcheck, tournament
    from socialbot.factcheck import FactCheck

    saved: list = []
    scheduled: list = []
    fin = _mk_finalist("A")
    try:
        _patch(saved, tournament, "concepts", lambda n=None, niche=None: [fin.topic])
        _patch(saved, tournament, "score_concepts", lambda ts, keep=None: [(fin.topic, 90.0)])
        _patch(saved, tournament, "develop", lambda topic, seconds=None: fin)
        _patch(saved, tournament, "polish", lambda f: None)
        _patch(saved, tournament, "judge", lambda survivors: [90.0])
        _patch(saved, factcheck, "vet_and_revise",
               lambda script, topic, **kw: (script, FactCheck(verdict="ok", summary="")))
        _patch(saved, tournament, "_materialize",
               lambda f: (_ for _ in ()).throw(AssertionError("must not render in dry run")))
        _patch(saved, tournament.pipeline, "schedule_item",
               lambda *a, **k: scheduled.append(a))
        _patch(saved, tournament.pipeline, "next_publish_times", lambda n, **kw: ["s"] * n)

        summary = tournament.run_batch(post=1, dry_run=True)
        assert summary["dry_run"] is True
        assert summary["posted"] == ["A"]      # plan lists the title, not an id
        assert scheduled == []                 # nothing uploaded
    finally:
        _restore(saved)


def test_parse_scores_tolerates_shapes():
    from socialbot.tournament import _NEUTRAL, _parse_scores

    assert _parse_scores({"scores": [{"index": 1, "score": 80}, {"index": 2, "score": 40}]}, 2) == [80.0, 40.0]
    assert _parse_scores({"rankings": [{"index": 2, "score": 99}]}, 2) == [_NEUTRAL, 99.0]
    assert _parse_scores([{"index": 1, "score": 10}], 1) == [10.0]
    assert _parse_scores({"1": 70, "2": 30}, 2) == [70.0, 30.0]
    # garbage / out-of-range indices fall back to neutral, never crash
    assert _parse_scores({}, 3) == [_NEUTRAL, _NEUTRAL, _NEUTRAL]
    assert _parse_scores({"scores": [{"index": 9, "score": 1}]}, 2) == [_NEUTRAL, _NEUTRAL]


def test_next_publish_times_are_future_and_staggered():
    from socialbot.pipeline import next_publish_times

    slots = next_publish_times(3, times=["14:00", "19:00", "00:00"], tz="UTC")
    assert len(slots) == 3
    assert all(s.endswith("Z") for s in slots)
    parsed = [datetime.fromisoformat(s.replace("Z", "+00:00")) for s in slots]
    now = datetime.now(timezone.utc)
    assert all(p > now for p in parsed)            # every slot is in the future
    assert parsed == sorted(parsed)                # staggered in order
    assert len(set(parsed)) == 3                   # distinct times

    # asking for more slots than configured times spills onto following days
    five = next_publish_times(5, times=["14:00", "19:00", "00:00"], tz="UTC")
    assert len(five) == 5
    five_p = [datetime.fromisoformat(s.replace("Z", "+00:00")) for s in five]
    assert len(set(five_p)) == 5


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} test(s) passed.")


if __name__ == "__main__":
    _run_all()
