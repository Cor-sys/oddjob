"""Tests for Phase 5: deterministic experiment-arm classification + length jitter.

Run with:  python -m tests.test_experiment   (or: pytest tests/test_experiment.py)
"""
from __future__ import annotations


def test_classify_cluster():
    from socialbot.experiment import classify_cluster

    assert classify_cluster("New NASA telescope spots a distant galaxy") == "space"
    assert classify_cluster("A new LLM model runs on a quantum chip") == "ai_tech"
    assert classify_cluster("Declassified UFO footage from a Navy pilot") == "ufo_uap"
    assert classify_cluster("A recipe for sourdough bread") == "other"
    # UFO/alien wins over space terms (alien spacecraft -> ufo_uap, not space)
    assert classify_cluster("alien spacecraft near the moon") == "ufo_uap"


def test_classify_hook():
    from socialbot.experiment import classify_hook

    assert classify_hook("What if the moon was hollow?") == "question"
    assert classify_hook("In 1977 a single signal changed everything") == "number"
    assert classify_hook("No one has ever returned from this place") == "negation"
    assert classify_hook("The deepest cave on Earth keeps going") == "statement"


def test_length_bucket():
    from socialbot.experiment import length_bucket

    assert length_bucket(32) == "short"
    assert length_bucket(38) == "mid"
    assert length_bucket(44) == "long"


def test_assign_arm_shape():
    from socialbot.experiment import assign_arm
    from socialbot.script import Script
    from socialbot.trends import Topic

    t = Topic(title="Voyager leaves the solar system", summary="space probe", keywords=["voyager", "nasa"])
    s = Script(topic_title=t.title, on_screen_title=t.title,
               narration="What lies past the edge?", description="d",
               hook_candidates=["What lies past the edge?"])
    arm = assign_arm(t, s, voice="en-US-ChristopherNeural", seconds=44)
    assert arm == {
        "topic_cluster": "space",
        "hook_style": "question",
        "length_bucket": "long",
        "voice": "en-US-ChristopherNeural",
    }


def test_jittered_seconds_in_range_and_strategy_biased():
    from socialbot.config import settings
    from socialbot.experiment import jittered_seconds, length_bucket

    lo, hi = settings.clip_seconds_min, settings.clip_seconds_max
    for _ in range(50):
        s = jittered_seconds()
        assert lo <= s <= hi

    # a strategy that favors "short" should keep lengths in the short bucket
    strat = {"weights": {"length_bucket": {"short": 1.4, "mid": 1.0, "long": 0.7}}}
    for _ in range(50):
        assert length_bucket(jittered_seconds(strat)) == "short"


def test_footage_affinity_prefers_showable_topics():
    from socialbot.experiment import footage_affinity
    from socialbot.trends import Topic

    space = Topic(title="Saturn's rings are vanishing", summary="Cassini saw ring rain", keywords=["saturn", "rings"])
    abstract = Topic(title="The economics of AI regulation", summary="policy debate over markets", keywords=["regulation", "policy"])
    plain = Topic(title="A new species of deep-sea fish", summary="found in a trench", keywords=["fish"])

    fa_space, fa_abstract, fa_plain = footage_affinity(space), footage_affinity(abstract), footage_affinity(plain)
    assert fa_space > fa_plain > fa_abstract        # NASA-rich > neutral > abstract stock-only
    assert fa_space > 1.0 and fa_abstract < 1.0
    assert 0.6 <= fa_abstract and fa_space <= 1.3   # clamped to range


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} test(s) passed.")


if __name__ == "__main__":
    _run_all()
