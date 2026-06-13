"""Tests for the footage quality gate (reject near-black/blown/solid frames).

Run with:  python -m tests.test_quality   (or: pytest tests/test_quality.py)

The decision logic is tested by stubbing _signalstats (no ffmpeg needed); one
optional integration check uses real ffmpeg if it's available, else skips.
"""
from __future__ import annotations

import tempfile
from pathlib import Path


def _tmpfile() -> Path:
    p = Path(tempfile.mkdtemp()) / "asset.bin"
    p.write_bytes(b"x" * 256)        # non-empty so the size guard passes
    return p


def _with_stats(stats):
    """Return a usable() result with _signalstats stubbed to `stats`."""
    from socialbot.media import quality
    old = quality._signalstats
    try:
        quality._signalstats = lambda p: stats
        return quality.usable(_tmpfile())
    finally:
        quality._signalstats = old


def test_rejects_near_black_empty():
    # dark on average AND nothing bright -> empty clip
    assert _with_stats({"YAVG": 16.0, "YMIN": 0.0, "YMAX": 143.0}) is False


def test_keeps_dark_space_with_bright_stars():
    # dark on average BUT bright element present (stars/planet edge) -> keep
    assert _with_stats({"YAVG": 16.0, "YMIN": 0.0, "YMAX": 255.0}) is True


def test_keeps_good_imagery():
    assert _with_stats({"YAVG": 48.0, "YMIN": 0.0, "YMAX": 255.0}) is True
    assert _with_stats({"YAVG": 130.0, "YMIN": 8.0, "YMAX": 255.0}) is True


def test_rejects_blown_white():
    assert _with_stats({"YAVG": 250.0, "YMIN": 240.0, "YMAX": 255.0}) is False


def test_rejects_near_uniform_solid():
    # tiny tonal range -> a near-solid colour card
    assert _with_stats({"YAVG": 120.0, "YMIN": 118.0, "YMAX": 138.0}) is False


def test_unmeasurable_is_usable_best_effort():
    # ffmpeg failed / unparseable -> never block a render
    assert _with_stats(None) is True
    assert _with_stats({"YMIN": 0.0}) is True      # no YAVG -> can't judge -> keep


def test_missing_or_empty_file_is_unusable():
    from socialbot.media import quality
    assert quality.usable(Path(tempfile.mkdtemp()) / "nope.bin") is False
    empty = Path(tempfile.mkdtemp()) / "empty.bin"
    empty.write_bytes(b"")
    assert quality.usable(empty) is False


def test_real_ffmpeg_black_vs_gradient():
    """Integration: real signalstats on a solid-black vs a gradient image. Skips if
    PIL/numpy or ffmpeg aren't available."""
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        print("  (skip: PIL/numpy unavailable)")
        return
    from socialbot.media import quality

    d = Path(tempfile.mkdtemp())
    black = d / "black.png"
    Image.new("RGB", (96, 96), (0, 0, 0)).save(black)
    grad = d / "grad.png"
    ramp = np.tile(np.linspace(0, 255, 96, dtype="uint8"), (96, 1))
    Image.fromarray(np.dstack([ramp, ramp[:, ::-1], np.flipud(ramp)])).save(grad)

    if quality._signalstats(black) is None:
        print("  (skip: ffmpeg unavailable)")
        return
    assert quality.usable(black) is False          # solid black -> rejected
    assert quality.usable(grad) is True            # full-range gradient -> kept


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} test(s) passed.")


if __name__ == "__main__":
    _run_all()
