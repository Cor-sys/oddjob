"""Tests for Phase 4: the persistent topic bank (carry unused concepts forward).

Run with:  python -m tests.test_topic_bank   (or: pytest tests/test_topic_bank.py)

Network-free: the bank file is redirected to a tempdir.
"""
from __future__ import annotations

import tempfile
from pathlib import Path


def _fresh():
    import socialbot.topic_bank as tb
    tb._BANK_FILE = Path(tempfile.mkdtemp()) / "topic_bank.json"
    return tb


def _topic(title: str, **kw):
    from socialbot.trends import Topic
    return Topic(title=title, summary=kw.get("summary", ""), keywords=kw.get("keywords", []))


def test_add_dedups_near_duplicate_and_keeps_max_score():
    tb = _fresh()
    tb.add_or_update([(_topic("Voyager leaves the solar system"), 70.0)])
    # near-duplicate title arrives later with a higher score
    tb.add_or_update([(_topic("Voyager leaves solar system now"), 85.0)])

    rows = tb.load()["concepts"]
    assert len(rows) == 1                # collapsed into one entry
    assert rows[0]["score"] == 85.0      # kept the higher score


def test_top_ranks_and_excludes_used():
    tb = _fresh()
    tb.add_or_update([
        (_topic("Alpha thing"), 60.0),
        (_topic("Beta object"), 90.0),
        (_topic("Gamma item"), 75.0),
    ])
    assert [t.title for t in tb.top(2)] == ["Beta object", "Gamma item"]

    tb.mark_used([_topic("Beta object")])
    assert [t.title for t in tb.top(2)] == ["Gamma item", "Alpha thing"]   # used one skipped


def test_decay_fades_unused_and_drops_below_floor():
    tb = _fresh()
    tb.add_or_update([
        (_topic("High score concept"), 100.0),   # 100*0.85 = 85 -> kept
        (_topic("Low score concept"), 22.0),     # 22*0.85 = 18.7 < 20 -> dropped
    ])
    result = tb.decay(factor=0.85, drop_below=20.0, max_concepts=10)

    titles = [e["title"] for e in tb.load()["concepts"]]
    assert "High score concept" in titles
    assert "Low score concept" not in titles
    assert result["kept"] == 1


def test_decay_drops_used_concepts():
    tb = _fresh()
    tb.add_or_update([(_topic("Already made"), 95.0)])
    tb.mark_used([_topic("Already made")])
    tb.decay()
    assert tb.load()["concepts"] == []   # used concepts are pruned (topic_history is canonical)


def test_decay_caps_bank_size():
    tb = _fresh()
    titles = [
        "Quantum tunneling reality", "Black hole jets", "Deep ocean creatures",
        "Ancient roman concrete", "Volcano lightning storms", "Neutron star collision",
        "Desert mirage physics", "Aurora magnetic fields",
    ]  # fully distinct titles so none collapse as near-duplicates
    tb.add_or_update([(_topic(t), 50.0 + i) for i, t in enumerate(titles)])
    assert len(tb.load()["concepts"]) == 8
    tb.decay(factor=1.0, drop_below=0.0, max_concepts=3)
    assert len(tb.load()["concepts"]) == 3   # capped to the size limit, highest kept


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} test(s) passed.")


if __name__ == "__main__":
    _run_all()
