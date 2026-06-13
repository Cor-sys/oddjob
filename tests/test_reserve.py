"""Tests for Phase 3: the reserve bank (recipes -> re-render with 0 LLM calls).

Run with:  python -m tests.test_reserve   (or: pytest tests/test_reserve.py)

Network-free: RESERVE_DIR is redirected to a tempdir and the render/schedule/
fact-check seams are stubbed, so no ffmpeg, HTTP, or LLM calls happen.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _redirect_reserve_dir():
    """Point reserve + review at a fresh tempdir; isolate coverage's scan dirs +
    cache too (render_reserve now consults topic_history.is_covered_meta). Returns
    the reserve tempdir."""
    import socialbot.reserve as reserve
    import socialbot.review as review
    import socialbot.topic_history as th

    tmp = Path(tempfile.mkdtemp())
    reserve.RESERVE_DIR = tmp
    review.RESERVE_DIR = tmp
    th.PUBLISHED_DIR = Path(tempfile.mkdtemp())
    th.PENDING_DIR = Path(tempfile.mkdtemp())
    th.RESERVE_DIR = tmp
    th._USED_FILE = Path(tempfile.mkdtemp()) / "used_topics.json"
    th._reset_scan_cache()
    return tmp


def _recipe_meta(title: str, *, verdict: str = "ok", score: float = 80.0, created: str | None = None):
    from socialbot.script import Beat, Script

    s = Script(
        topic_title=title, on_screen_title=title,
        narration="The sky scatters blue light.", description="d",
        shot_list=[Beat(text="The sky scatters blue light.", query="blue sky", kind="stock")],
    )
    meta = {
        "topic": {"title": title, "summary": "sum", "why_trending": "", "keywords": ["sky"], "sources": []},
        "research": {},
        "topic_title": title,
        "on_screen_title": title,
        "script": s.to_dict(),
        "factcheck": {"verdict": verdict, "summary": "", "claims": [], "sources": []},
        "judge_score": score,
    }
    if created:
        meta["created_at"] = created
    return meta


def test_bank_list_best_are_score_ordered():
    import socialbot.reserve as reserve

    _redirect_reserve_dir()
    reserve.bank(_recipe_meta("low", score=62))
    reserve.bank(_recipe_meta("high", score=95))
    reserve.bank(_recipe_meta("mid", score=78))

    listed = reserve.list_reserve()
    assert [it.meta["topic_title"] for it in listed] == ["high", "mid", "low"]

    top2 = reserve.best(2)
    assert [it.meta["topic_title"] for it in top2] == ["high", "mid"]

    # exclude skips an id
    excl = {listed[0].id}
    assert reserve.best(2, exclude=excl)[0].meta["topic_title"] == "mid"


def test_prune_keeps_top_n():
    import socialbot.reserve as reserve

    _redirect_reserve_dir()
    for i in range(5):
        reserve.bank(_recipe_meta(f"r{i}", score=50 + i))   # r4 highest ... r0 lowest

    removed = reserve.prune_reserve(keep=2)
    assert removed == 3
    survivors = [it.meta["topic_title"] for it in reserve.list_reserve()]
    assert survivors == ["r4", "r3"]


def test_render_reserve_renders_and_schedules_when_publishable():
    import socialbot.pipeline as pipeline
    import socialbot.reserve as reserve

    _redirect_reserve_dir()
    item = reserve.bank(_recipe_meta("blue-sky", verdict="ok", score=88))

    rendered: list = []
    scheduled: list = []
    old_render, old_sched = pipeline._render, pipeline.schedule_item
    try:
        pipeline._render = lambda it, script, topic: (
            rendered.append((it.id, script.topic_title, topic.title)),
            it.meta.__setitem__("clip", "clip.mp4"),
        )
        pipeline.schedule_item = lambda it, at, **kw: scheduled.append((it.id, at))

        reserve.render_reserve(item.id, publish_at="2099-01-01T00:00:00Z")
        assert rendered and rendered[0][1] == "blue-sky"   # rehydrated the right script
        assert rendered[0][2] == "blue-sky"                # and reconstructed the Topic
        assert scheduled == [(item.id, "2099-01-01T00:00:00Z")]
    finally:
        pipeline._render, pipeline.schedule_item = old_render, old_sched


def test_render_reserve_skips_schedule_when_not_publishable():
    import socialbot.pipeline as pipeline
    import socialbot.reserve as reserve

    _redirect_reserve_dir()
    # needs_review on a NON-speculative topic -> not auto-publishable
    item = reserve.bank(_recipe_meta("uncertain", verdict="needs_review", score=70))

    scheduled: list = []
    old_render, old_sched = pipeline._render, pipeline.schedule_item
    try:
        pipeline._render = lambda it, script, topic: it.meta.__setitem__("clip", "clip.mp4")
        pipeline.schedule_item = lambda it, at, **kw: scheduled.append((it.id, at))

        out = reserve.render_reserve(item.id, publish_at="2099-01-01T00:00:00Z")
        assert scheduled == []                 # rendered but NOT scheduled
        assert out.meta.get("clip") == "clip.mp4"
    finally:
        pipeline._render, pipeline.schedule_item = old_render, old_sched


def test_stale_recipe_is_revetted_and_rejection_blocks_render():
    import socialbot.factcheck as factcheck
    import socialbot.pipeline as pipeline
    import socialbot.reserve as reserve
    from socialbot.factcheck import FactCheck

    _redirect_reserve_dir()
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    item = reserve.bank(_recipe_meta("stale", verdict="ok", score=90, created=old))

    revet_calls: list = []
    old_vet, old_render = factcheck.vet, pipeline._render
    try:
        def _vet(script):
            revet_calls.append(script.topic_title)
            return FactCheck(verdict="rejected", summary="facts no longer hold")
        factcheck.vet = _vet
        pipeline._render = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not render"))

        raised = False
        try:
            reserve.render_reserve(item.id, publish_at="2099-01-01T00:00:00Z")
        except RuntimeError:
            raised = True
        assert raised                          # stale + now-rejected -> refuse to render
        assert revet_calls == ["stale"]        # it was re-fact-checked

        fresh = reserve.review.get(item.id)
        assert fresh.status == reserve.review.REJECTED
    finally:
        factcheck.vet, pipeline._render = old_vet, old_render


def test_render_reserve_skips_already_aired_story():
    import json

    import socialbot.pipeline as pipeline
    import socialbot.reserve as reserve
    import socialbot.topic_history as th

    _redirect_reserve_dir()   # also isolates th.PUBLISHED_DIR/PENDING_DIR to empty tmpdirs
    item = reserve.bank(_recipe_meta("blue-sky", verdict="ok", score=88))

    # A published artifact for the SAME story — a later batch aired it.
    d = th.PUBLISHED_DIR / "20260101-000000_aired"
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(
        json.dumps({"topic_title": "blue-sky", "topic": {"keywords": ["sky"]}, "status": "published"}),
        encoding="utf-8",
    )
    th._reset_scan_cache()

    scheduled: list = []
    old_render, old_sched = pipeline._render, pipeline.schedule_item
    try:
        pipeline._render = lambda it, s, t: it.meta.__setitem__("clip", "clip.mp4")
        pipeline.schedule_item = lambda it, at, **kw: scheduled.append(it.id)

        reserve.render_reserve(item.id, publish_at="2099-01-01T00:00:00Z")
        assert scheduled == []                     # already aired -> not re-scheduled

        reserve.render_reserve(item.id, publish_at="2099-01-01T00:00:00Z", force=True)
        assert scheduled == [item.id]              # force bypasses the coverage check
    finally:
        pipeline._render, pipeline.schedule_item = old_render, old_sched


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} test(s) passed.")


if __name__ == "__main__":
    _run_all()
