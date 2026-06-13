"""Tests for the discovery avoid-block (Layer ③): recently-covered titles are
folded into the discover prompt so the model avoids them at the source.

Run with:  python -m tests.test_trends_avoid   (or: pytest tests/test_trends_avoid.py)

Network-free: trends.grounded_json is stubbed to capture the prompt, so no LLM call.
"""
from __future__ import annotations


def _capture_discover(**kwargs):
    """Call trends.discover with grounded_json stubbed; return the prompt it built."""
    import socialbot.trends as trends

    captured = {}
    old = trends.grounded_json
    try:
        def fake(prompt, *, system=None, model=None):
            captured["prompt"] = prompt
            return [], []
        trends.grounded_json = fake
        trends.discover(**kwargs)
    finally:
        trends.grounded_json = old
    return captured["prompt"]


def test_discover_includes_avoid_block():
    prompt = _capture_discover(count=3, avoid=["ISS Air Leak Worsens", "SpaceX Mars Colony Doubts"])
    assert "AVOID these stories" in prompt
    assert "ISS Air Leak Worsens" in prompt
    assert "SpaceX Mars Colony Doubts" in prompt


def test_discover_no_avoid_block_when_empty():
    assert "AVOID these stories" not in _capture_discover(count=3, avoid=None)
    assert "AVOID these stories" not in _capture_discover(count=3, avoid=[])


def test_discover_signature_backcompat():
    # Old call style (no avoid) still works and builds a normal prompt.
    prompt = _capture_discover(count=5, niche="space")
    assert "find 5 stories" in prompt
    assert "AVOID these stories" not in prompt


def test_discover_avoid_is_capped_and_sanitized():
    titles = ["Has\na newline in it"] + [f"Story number {i}" for i in range(50)]
    prompt = _capture_discover(count=3, avoid=titles)
    # capped at 30 entries
    assert "Story number 0" in prompt
    assert "Story number 40" not in prompt
    # internal newline in a title is collapsed, not left to break the block
    assert "Has\na newline" not in prompt
    assert "Has a newline in it" in prompt


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} test(s) passed.")


if __name__ == "__main__":
    _run_all()
