"""Tests for the footage router's source selection (the part that decides whether a
beat gets NASA/Commons real imagery or Pexels stock).

Run with:  python -m tests.test_router   (or: pytest tests/test_router.py)

Network-free: nasa/commons/stock fetchers are stubbed to record which source the
router reaches for, in what order.
"""
from __future__ import annotations

from pathlib import Path


def _patch(saved, obj, name, val):
    saved.append((obj, name, getattr(obj, name)))
    setattr(obj, name, val)


def _restore(saved):
    for obj, name, old in reversed(saved):
        setattr(obj, name, old)


def _record_sources(saved, *, nasa_ret, stock_ret, commons_ret, calls):
    from socialbot.media import router

    def nasa_fetch(kws, d, n):
        calls.append(("nasa", tuple(kws)))
        return list(nasa_ret)

    def stock_fetch(kws, d, n):
        calls.append(("stock", tuple(kws)))
        return list(stock_ret)

    def commons_fetch(kws, d, n):
        calls.append(("commons", tuple(kws)))
        return list(commons_ret)

    _patch(saved, router.nasa, "fetch_media", nasa_fetch)
    _patch(saved, router.stock, "fetch_broll", stock_fetch)
    _patch(saved, router.commons, "fetch_media", commons_fetch)


def test_space_topic_stock_beat_goes_to_nasa_not_pexels():
    from socialbot.media import router
    from socialbot.script import Beat

    saved, calls = [], []
    try:
        _record_sources(saved, nasa_ret=[Path("nasa.jpg")], stock_ret=[], commons_ret=[], calls=calls)
        beat = Beat(text="t", query="rocket launch", kind="stock")
        path, credit = router._fetch_for_beat(beat, True, Path("/tmp/x"))
        assert path == Path("nasa.jpg")               # got real NASA imagery
        assert calls[0][0] == "nasa"                  # NASA tried FIRST
        assert not any(c[0] == "stock" for c in calls)  # never fell to Pexels
    finally:
        _restore(saved)


def test_space_topic_falls_back_to_nasa_space_terms_before_pexels():
    from socialbot.media import router
    from socialbot.script import Beat

    saved, calls = [], []
    try:
        # NASA returns nothing for the beat query, something for the space fallback.
        def nasa_fetch(kws, d, n):
            calls.append(("nasa", tuple(kws)))
            return [Path("fallback.jpg")] if kws[0] in router._SPACE_FALLBACKS else []
        _patch(saved, router.nasa, "fetch_media", nasa_fetch)
        _patch(saved, router.stock, "fetch_broll",
               lambda kws, d, n: (calls.append(("stock", tuple(kws))), [])[1])
        _patch(saved, router.commons, "fetch_media",
               lambda kws, d, n: (calls.append(("commons", tuple(kws))), [])[1])

        beat = Beat(text="t", query="some niche craft", kind="auto")
        path, _ = router._fetch_for_beat(beat, True, Path("/tmp/x"))
        assert path == Path("fallback.jpg")           # space fallback imagery
        assert [c[0] for c in calls] == ["nasa", "nasa"]  # query, then fallback
        assert not any(c[0] == "stock" for c in calls)    # Pexels never reached
    finally:
        _restore(saved)


def test_nonspace_topic_goes_straight_to_pexels():
    from socialbot.media import router
    from socialbot.script import Beat

    saved, calls = [], []
    try:
        _record_sources(saved, nasa_ret=[], stock_ret=[Path("clip.mp4")], commons_ret=[], calls=calls)
        beat = Beat(text="t", query="data center servers", kind="stock")
        path, _ = router._fetch_for_beat(beat, False, Path("/tmp/x"))
        assert path == Path("clip.mp4")
        assert calls[0][0] == "stock"                 # straight to Pexels, no NASA
        assert not any(c[0] == "nasa" for c in calls)
    finally:
        _restore(saved)


def test_entity_beat_prefers_commons():
    from socialbot.media import router
    from socialbot.script import Beat
    from socialbot.media.commons import Asset

    saved, calls = [], []
    try:
        def commons_fetch(kws, d, n):
            calls.append(("commons", tuple(kws)))
            return [Asset(path=Path("psyche.jpg"), credit="NASA (CC BY)")]
        _patch(saved, router.commons, "fetch_media", commons_fetch)
        _patch(saved, router.nasa, "fetch_media",
               lambda kws, d, n: (calls.append(("nasa", tuple(kws))), [])[1])
        _patch(saved, router.stock, "fetch_broll",
               lambda kws, d, n: (calls.append(("stock", tuple(kws))), [])[1])

        beat = Beat(text="t", query="Psyche spacecraft", kind="entity")
        path, credit = router._fetch_for_beat(beat, True, Path("/tmp/x"))
        assert path == Path("psyche.jpg")
        assert credit == "NASA (CC BY)"               # Commons credit carried through
        assert calls[0][0] == "commons"
    finally:
        _restore(saved)


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} test(s) passed.")


if __name__ == "__main__":
    _run_all()
