"""Tests for Phase 3: the best-of-N tournament orchestration + scheduling math.

Run with:  python -m tests.test_tournament   (or: pytest tests/test_tournament.py)

Network-free: every LLM/render/upload seam is stubbed, so run_batch is exercised
as pure orchestration logic (post/bank split, fact-check gate, the anchored quality
floor, reserve shortfall fill, manual-review fallback).
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
    """Drive run_batch (schedule mode) with stubbed stages; returns
    (summary, scheduled, banked_titles)."""
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
        # judge now returns (weighted_total, subscores) per survivor.
        _patch(saved, tournament, "judge",
               lambda survivors: [(judges[f.topic.title], {}) for f in survivors])
        _patch(saved, factcheck, "vet_and_revise",
               lambda script, topic, **kw: (script, FactCheck(verdict=verdicts[script.topic_title], summary="")))

        def _fake_materialize(f):
            return types.SimpleNamespace(id=f"item-{f.topic.title}", meta={})

        def _fake_bank(meta):
            banked_titles.append(meta["topic_title"])
            return types.SimpleNamespace(id=f"reserve-{meta['topic_title']}")

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


def test_run_batch_posts_above_floor_banks_rest_drops_low_and_rejected():
    # A=90 posts; B=70 above bank floor (banked, post=1); C=50 below bank floor
    # (dropped); D rejected by the fact-check gate.
    summary, scheduled, banked = _run_funnel(
        verdicts={"A": "ok", "B": "ok", "C": "ok", "D": "rejected"},
        judges={"A": 90.0, "B": 70.0, "C": 50.0, "D": 0.0},
        post=1,
    )
    assert summary["posted"] == ["item-A"]          # only the top postable winner
    assert summary["banked"] == ["reserve-B"]       # 70 >= bank floor (55); not the posted one
    assert "C" not in banked                        # 50 < bank floor -> dropped
    assert summary["rejected"] == 1                 # D removed by the fact-check gate
    assert scheduled == [("item-A", "slot-0")]


def test_below_floor_winner_not_posted_posts_fewer():
    # A fact-check-CLEAN winner that's below the post floor must NOT post; with an
    # empty reserve, the batch posts fewer rather than shipping filler.
    summary, scheduled, banked = _run_funnel(
        verdicts={"B": "ok"},
        judges={"B": 58.0},      # >= bank floor (55), < post floor (65)
        post=1,
    )
    assert summary["posted"] == []              # not posted — below the quality bar
    assert summary["banked"] == ["reserve-B"]   # still worth banking for a future slot
    assert scheduled == []


def test_reserve_fill_requires_bank_floor():
    from socialbot import factcheck, tournament
    from socialbot.factcheck import FactCheck

    # Only survivor is needs_review (non-speculative) -> not auto-postable but
    # bankable; the open slot is filled from a reserve recipe that clears the bank
    # floor. A second reserve recipe below the floor must be skipped.
    saved: list = []
    scheduled: list = []
    fin = _mk_finalist("E")
    try:
        _patch(saved, tournament, "concepts", lambda n=None, niche=None: [fin.topic])
        _patch(saved, tournament, "score_concepts", lambda ts, keep=None: [(fin.topic, 70.0)])
        _patch(saved, tournament, "develop", lambda topic, seconds=None: fin)
        _patch(saved, tournament, "polish", lambda f: None)
        _patch(saved, tournament, "judge", lambda survivors: [(70.0, {})])
        _patch(saved, factcheck, "vet_and_revise",
               lambda script, topic, **kw: (script, FactCheck(verdict="needs_review", summary="")))
        _patch(saved, tournament, "_materialize",
               lambda f: types.SimpleNamespace(id=f"item-{f.topic.title}", meta={}))
        _patch(saved, tournament.reserve, "bank",
               lambda meta: types.SimpleNamespace(id="reserve-E"))
        _patch(saved, tournament.reserve, "best",
               lambda n, exclude=None: [
                   types.SimpleNamespace(id="recipe-low",
                       meta={"factcheck": {"verdict": "ok"}, "judge_score": 40}),   # below floor -> skip
                   types.SimpleNamespace(id="recipe-ok",
                       meta={"factcheck": {"verdict": "ok"}, "judge_score": 80}),    # fills the slot
               ])
        _patch(saved, tournament.reserve, "render_reserve",
               lambda rid, publish_at=None: scheduled.append((rid, publish_at)))
        _patch(saved, tournament.pipeline, "schedule_item",
               lambda item, slot, **kw: scheduled.append((item.id, slot)))
        _patch(saved, tournament.pipeline, "next_publish_times",
               lambda n, **kw: [f"slot-{i}" for i in range(n)])
        _patch(saved, tournament.topic_history, "remember", lambda topics: None)

        summary = tournament.run_batch(post=1)
        assert summary["posted"] == ["recipe-ok"]    # the >= floor recipe filled the slot
        assert summary["banked"] == ["reserve-E"]
        assert scheduled == [("recipe-ok", "slot-0")]
    finally:
        _restore(saved)


def test_no_schedule_below_floor_uploads_best_for_review():
    from socialbot import factcheck, tournament
    from socialbot.factcheck import FactCheck

    # Manual-review mode: nothing clears the floor, so the single best survivor is
    # uploaded for REVIEW ONLY (flagged) instead of silently dropped.
    saved: list = []
    uploaded: list = []
    fin = _mk_finalist("B")
    try:
        _patch(saved, tournament, "concepts", lambda n=None, niche=None: [fin.topic])
        _patch(saved, tournament, "score_concepts", lambda ts, keep=None: [(fin.topic, 60.0)])
        _patch(saved, tournament, "develop", lambda topic, seconds=None: fin)
        _patch(saved, tournament, "polish", lambda f: None)
        _patch(saved, tournament, "judge", lambda survivors: [(58.0, {})])   # below post floor
        _patch(saved, factcheck, "vet_and_revise",
               lambda script, topic, **kw: (script, FactCheck(verdict="ok", summary="")))
        _patch(saved, tournament, "_materialize",
               lambda f: types.SimpleNamespace(id=f"item-{f.topic.title}", meta={}))
        _patch(saved, tournament, "_upload_private", lambda item: uploaded.append(item.id))
        _patch(saved, tournament.reserve, "bank",
               lambda meta: types.SimpleNamespace(id="reserve-B"))
        _patch(saved, tournament.topic_history, "remember", lambda topics: None)

        summary = tournament.run_batch(post=1, schedule=False)
        assert summary["posted"] == ["item-B"]               # surfaced for review
        assert summary.get("below_floor_review") == "item-B"
        assert uploaded == ["item-B"]
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
        _patch(saved, tournament, "judge", lambda survivors: [(90.0, {})])
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


def test_parse_graded_computes_weighted_total_from_subscores():
    from socialbot.tournament import _JUDGE_DIMS, _NEUTRAL, _parse_graded

    data = {"rankings": [
        {"index": 1, "hook": 90, "escalation": 80, "specificity": 80, "payoff_loop": 70, "filmability": 60},
        {"index": 2, "hook": 40, "escalation": 45, "specificity": 50, "payoff_loop": 40, "filmability": 50},
    ]}
    res = _parse_graded(data, 2, _JUDGE_DIMS)
    # total = weighted mean of subscores (computed in code, not trusting any 'score')
    assert res[0][0] == 79.5          # 90*.3 + 80*.25 + 80*.2 + 70*.15 + 60*.1
    assert res[0][1]["hook"] == 90.0
    assert res[1][0] == 44.2
    # no subscores -> fall back to a flat 'score'
    assert _parse_graded({"scores": [{"index": 1, "score": 72}]}, 1, _JUDGE_DIMS) == [(72.0, {})]
    # garbage / empty -> neutral, never crash
    assert _parse_graded({}, 2, _JUDGE_DIMS) == [(_NEUTRAL, {}), (_NEUTRAL, {})]
    # the model's freeform 'score' is IGNORED when subscores are present
    only = _parse_graded({"rankings": [{"index": 1, "hook": 100, "escalation": 100,
                          "specificity": 100, "payoff_loop": 100, "filmability": 100, "score": 3}]}, 1, _JUDGE_DIMS)
    assert only[0][0] == 100.0


def test_effective_post_floor_takes_max_of_config_and_learned():
    from socialbot.config import settings
    from socialbot.tournament import _effective_post_floor

    assert _effective_post_floor({}) == settings.post_score_floor
    assert _effective_post_floor({"post_floor": settings.post_score_floor + 10}) == settings.post_score_floor + 10
    assert _effective_post_floor({"post_floor": 0}) == settings.post_score_floor
    assert _effective_post_floor({"post_floor": "bad"}) == settings.post_score_floor


def test_directives_block_reflects_learned_strategy():
    from socialbot import tournament

    saved: list = []
    try:
        _patch(saved, tournament.analytics, "load_strategy",
               lambda: {"directives": ["Lead with the single number."]})
        block = tournament._directives_block()
        assert "Lead with the single number." in block
        assert "RETAINS" in block
        _patch(saved, tournament.analytics, "load_strategy", lambda: {})
        assert tournament._directives_block() == ""      # no directives -> no block
    finally:
        _restore(saved)


def test_parse_scores_tolerates_shapes():
    # The legacy flat parser is kept as a fallback and must still work.
    from socialbot.tournament import _NEUTRAL, _parse_scores

    assert _parse_scores({"scores": [{"index": 1, "score": 80}, {"index": 2, "score": 40}]}, 2) == [80.0, 40.0]
    assert _parse_scores({"rankings": [{"index": 2, "score": 99}]}, 2) == [_NEUTRAL, 99.0]
    assert _parse_scores([{"index": 1, "score": 10}], 1) == [10.0]
    assert _parse_scores({"1": 70, "2": 30}, 2) == [70.0, 30.0]
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
