"""Tests for Phase 5: analytics ingest + join + strategy synthesis.

Run with:  python -m tests.test_analytics   (or: pytest tests/test_analytics.py)

Network-free: the analytics/strategy files are redirected to a tempdir, the
published-video index is stubbed, and the directive-synthesis LLM call is stubbed.
"""
from __future__ import annotations

import tempfile
import types
from pathlib import Path

_CSV = """Video,Video title,Views,Average percentage viewed (%),Average view duration,Impressions click-through rate (%),Likes
abc123,My Great Short,1000,55.5,0:23,4.2,50
def456,Another One,500,40,0:15,3.1,20
Total,,1500,48,0:19,3.8,70
"""


def _write_csv() -> str:
    p = Path(tempfile.mkdtemp()) / "studio.csv"
    p.write_text(_CSV, encoding="utf-8")
    return str(p)


def test_ingest_maps_headers_and_skips_total():
    from socialbot import analytics

    rows = analytics.ingest(_write_csv())
    assert [r["video_id"] for r in rows] == ["abc123", "def456"]   # Total row skipped
    r0 = rows[0]
    assert r0["views"] == 1000.0
    assert r0["avg_view_pct"] == 55.5
    assert r0["avg_view_seconds"] == 23.0                          # "0:23" -> 23s
    assert r0["ctr"] == 4.2
    assert r0["likes"] == 50.0


def test_ingest_raises_when_no_video_column():
    from socialbot import analytics

    p = Path(tempfile.mkdtemp()) / "bad.csv"
    p.write_text("Title,Views\nfoo,100\n", encoding="utf-8")
    raised = False
    try:
        analytics.ingest(str(p))
    except RuntimeError as e:
        raised = "Title" in str(e)        # error lists the detected headers
    assert raised


def test_join_is_idempotent_per_day():
    from socialbot import analytics

    analytics.ANALYTICS_FILE = Path(tempfile.mkdtemp()) / "analytics.jsonl"

    item = types.SimpleNamespace(
        id="20260101-000000_short",
        meta={"experiment_arm": {"topic_cluster": "space", "hook_style": "question",
                                 "length_bucket": "long", "voice": "v"},
              "topic_title": "T", "created_at": "2026-01-01T00:00:00Z"},
    )
    old_index = analytics._published_index
    try:
        analytics._published_index = lambda: {"abc123": item}
        rows = [{"video_id": "abc123", "views": 1000, "avg_view_pct": 55.5,
                 "avg_view_seconds": 23, "ctr": 4.2, "likes": 50},
                {"video_id": "zzz999", "views": 1, "avg_view_pct": 1,    # not one of ours
                 "avg_view_seconds": 1, "ctr": 1, "likes": 0}]
        assert analytics.join(rows) == 1          # only the posted video matched
        assert analytics.join(rows) == 0          # same day -> idempotent, no dup
        recs = analytics._read_jsonl(analytics.ANALYTICS_FILE)
        assert len(recs) == 1
        assert recs[0]["experiment_arm"]["topic_cluster"] == "space"
    finally:
        analytics._published_index = old_index


def _seed_records(analytics, specs):
    """specs: list of (cluster, pct) -> write analytics.jsonl records."""
    import json
    lines = []
    for i, (cluster, pct) in enumerate(specs):
        lines.append(json.dumps({
            "video_id": f"v{i}", "item_id": f"i{i}", "as_of": "2026-01-01",
            "experiment_arm": {"topic_cluster": cluster, "hook_style": "statement",
                               "length_bucket": "mid", "voice": "v"},
            "metrics": {"avg_view_pct": pct, "views": 100, "avg_view_seconds": 20,
                        "ctr": 3.0, "likes": 5},
        }))
    analytics.ANALYTICS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_synthesize_weights_favor_high_retention_cluster():
    from socialbot import analytics

    tmp = Path(tempfile.mkdtemp())
    analytics.ANALYTICS_FILE = tmp / "analytics.jsonl"
    analytics.STRATEGY_FILE = tmp / "strategy.json"
    # 10 videos: space retains well (60%), ai_tech poorly (30%)
    _seed_records(analytics, [("space", 60)] * 5 + [("ai_tech", 30)] * 5)

    old_dir = analytics._directives
    try:
        analytics._directives = lambda agg: (["Favor space topics."], "")
        strat = analytics.synthesize()
        assert strat["sample_size"] == 10
        w = strat["weights"]["topic_cluster"]
        assert w["space"] > 1.0
        assert w["space"] > w["ai_tech"]          # higher retention -> higher weight
        # round-trips through the loader and the cluster_weight helper
        loaded = analytics.load_strategy()
        assert analytics.cluster_weight(loaded, "space") == w["space"]
        assert analytics.cluster_weight(loaded, "unknown") == 1.0
    finally:
        analytics._directives = old_dir


def test_small_sample_weights_are_clamped_toward_neutral():
    from socialbot import analytics

    tmp = Path(tempfile.mkdtemp())
    analytics.ANALYTICS_FILE = tmp / "analytics.jsonl"
    analytics.STRATEGY_FILE = tmp / "strategy.json"
    # only 4 videos -> below the trust threshold -> weights pulled toward 1.0
    _seed_records(analytics, [("space", 80)] * 2 + [("ai_tech", 20)] * 2)

    old_dir = analytics._directives
    try:
        analytics._directives = lambda agg: (["generic"], "small sample")
        strat = analytics.synthesize()
        w = strat["weights"]["topic_cluster"]
        # raw space ratio would be ~1.6; clamped it should sit close to 1.0
        assert abs(w["space"] - 1.0) < 0.2
    finally:
        analytics._directives = old_dir


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} test(s) passed.")


if __name__ == "__main__":
    _run_all()
