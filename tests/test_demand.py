"""Tests for Phase 4: the free YouTube-autocomplete demand signal + niche filter.

Run with:  python -m tests.test_demand   (or: pytest tests/test_demand.py)

Network-free: demand.suggest (HTTP) and the niche-filter LLM call are stubbed.
"""
from __future__ import annotations


def test_seeds_are_keyword_anchored():
    from socialbot.demand import _seeds
    from socialbot.trends import Topic

    t = Topic(title="Voyager probe leaves the solar system", summary="",
              keywords=["voyager 1", "heliopause", "nasa probe", "extra one", "extra two"])
    assert _seeds(t) == ["voyager 1", "heliopause", "nasa probe"]   # capped at 3, from keywords

    t2 = Topic(title="A concept with no keywords", summary="", keywords=[])
    assert _seeds(t2) == ["A concept with no keywords"]             # falls back to the title


def test_demand_score_rewards_overlap():
    from socialbot.demand import _demand_score
    from socialbot.trends import Topic

    t = Topic(title="Voyager probe", summary="", keywords=["voyager"])
    assert _demand_score(t, []) == 0.0
    scored = _demand_score(t, ["voyager 1 location", "voyager interstellar", "unrelated thing"])
    assert scored > 0


def test_enrich_drops_off_niche_and_attaches_demand():
    from socialbot import demand
    from socialbot.trends import Topic

    concepts = [
        Topic(title="Black hole jets", summary="space jets", keywords=["black hole"]),
        Topic(title="Best pizza recipe", summary="food", keywords=["pizza"]),
    ]
    old_suggest, old_json = demand.suggest, demand.json_call
    try:
        demand.suggest = lambda seed: [f"{seed} explained", f"{seed} facts"]
        demand.json_call = lambda prompt, system=None, model=None: {
            "verdicts": [{"index": 1, "on_niche": True}, {"index": 2, "on_niche": False}]
        }
        out = demand.enrich(concepts, "space and astronomy")
        assert [t.title for t in out] == ["Black hole jets"]   # off-niche pizza dropped
        assert out[0].demand > 0                               # demand attached
        assert out[0].phrasings                                # real phrasings captured
    finally:
        demand.suggest, demand.json_call = old_suggest, old_json


def test_enrich_degrades_when_autocomplete_empty():
    from socialbot import demand
    from socialbot.trends import Topic

    concepts = [Topic(title="Quiet topic", summary="s", keywords=["quiet"])]
    old_suggest, old_json = demand.suggest, demand.json_call
    try:
        demand.suggest = lambda seed: []                        # endpoint offline/blocked
        demand.json_call = lambda *a, **k: {"verdicts": [{"index": 1, "on_niche": True}]}
        out = demand.enrich(concepts, "anything")
        assert len(out) == 1
        assert out[0].demand == 0.0                             # unknown demand, still flows through
    finally:
        demand.suggest, demand.json_call = old_suggest, old_json


def test_niche_filter_keeps_all_on_llm_failure():
    from socialbot import demand
    from socialbot.trends import Topic

    concepts = [Topic(title="One", summary="", keywords=[]), Topic(title="Two", summary="", keywords=[])]
    old_json = demand.json_call
    try:
        def _boom(*a, **k):
            raise RuntimeError("model down")
        demand.json_call = _boom
        kept = demand._niche_filter(concepts, "niche")
        assert kept == concepts                                 # never lose the pool on failure
    finally:
        demand.json_call = old_json


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} test(s) passed.")


if __name__ == "__main__":
    _run_all()
