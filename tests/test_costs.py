"""Tests for the spend ledger (socialbot.costs).

Run with:  python -m tests.test_costs   (or: pytest tests/test_costs.py)

These exercise pricing, attribution, recording, and the summary aggregation
without touching the network or the real Gemini SDK.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace


def _fresh_costs(tmp: Path):
    """Import costs with its ledger pointed at a temp file."""
    from socialbot import costs

    costs.COSTS_FILE = tmp / "costs.jsonl"
    return costs


def _fake_response(prompt_tokens: int, output_tokens: int, thoughts: int = 0):
    """Mimic a google-genai response's usage_metadata shape."""
    usage = SimpleNamespace(
        prompt_token_count=prompt_tokens,
        candidates_token_count=output_tokens,
        thoughts_token_count=thoughts,
    )
    return SimpleNamespace(usage_metadata=usage)


def test_pricing_and_suffix_matching():
    costs = _fresh_costs(Path(tempfile.mkdtemp()))
    # 1M in + 1M out on flash = 0.30 + 2.50
    assert abs(costs.estimate_cost("gemini-2.5-flash", 1_000_000, 1_000_000) - 2.80) < 1e-9
    # flash-lite is cheaper, and a version suffix still resolves (longest match)
    assert abs(costs.estimate_cost("gemini-2.5-flash-lite-002", 1_000_000, 0) - 0.10) < 1e-9
    # unknown model -> no price, no crash
    assert costs.estimate_cost("mystery-model", 1_000_000, 1_000_000) == 0.0


def test_env_price_override(monkeypatch=None):
    costs = _fresh_costs(Path(tempfile.mkdtemp()))
    os.environ["GEMINI_PRICES"] = '{"gemini-2.5-flash":{"input":1.0,"output":2.0}}'
    try:
        assert abs(costs.estimate_cost("gemini-2.5-flash", 1_000_000, 1_000_000) - 3.0) < 1e-9
    finally:
        del os.environ["GEMINI_PRICES"]


def test_track_attribution_and_nesting():
    costs = _fresh_costs(Path(tempfile.mkdtemp()))
    with costs.track(topic="Mars water") as video:
        with costs.track(stage="script"):
            costs.record_llm("gemini-2.5-flash", _fake_response(1_000_000, 1_000_000))
        with costs.track(stage="factcheck"):
            costs.record_llm("gemini-2.5-flash-lite", _fake_response(1_000_000, 0))

    # the outer (video) frame sees both calls
    assert video.llm_calls == 2
    assert video.input_tokens == 2_000_000
    # 2.80 (flash) + 0.10 (flash-lite) = 2.90
    assert abs(video.cost_usd - 2.90) < 1e-9

    # ledger has one line per call, each tagged with topic + its own stage
    entries = costs._read_ledger()
    assert len(entries) == 2
    assert all(e["topic"] == "Mars water" for e in entries)
    assert {e["stage"] for e in entries} == {"script", "factcheck"}


def test_youtube_upload_is_free_but_logged():
    costs = _fresh_costs(Path(tempfile.mkdtemp()))
    with costs.track(item_id="vid1"):
        costs.record_youtube_upload("abc123", file_size=4096)

    s = costs.summary()
    assert s["youtube"]["uploads"] == 1
    assert s["youtube"]["quota_units_used"] == costs.YOUTUBE_UPLOAD_QUOTA_UNITS
    assert s["youtube"]["posting_cost_usd"] == 0.0  # the Data API costs no money


def test_summary_aggregation():
    costs = _fresh_costs(Path(tempfile.mkdtemp()))
    with costs.track(stage="trends"):
        costs.record_llm("gemini-2.5-flash-lite", _fake_response(1_000_000, 0))  # $0.10
    with costs.track(stage="script"):
        costs.record_llm("gemini-2.5-flash", _fake_response(0, 1_000_000))       # $2.50
    costs.record_youtube_upload("xyz")

    s = costs.summary()
    assert abs(s["total_estimated_cost_usd"] - 2.60) < 1e-9
    assert s["llm_calls"] == 2
    assert abs(s["by_stage"]["trends"] - 0.10) < 1e-9
    assert abs(s["by_stage"]["script"] - 2.50) < 1e-9
    assert s["by_model"]["gemini-2.5-flash"]["calls"] == 1
    assert s["youtube"]["uploads"] == 1


def test_zero_usage_is_not_recorded():
    costs = _fresh_costs(Path(tempfile.mkdtemp()))
    costs.record_llm("gemini-2.5-flash", _fake_response(0, 0))
    assert costs._read_ledger() == []


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} test(s) passed.")


if __name__ == "__main__":
    _run_all()
