"""Tests for Phase 2 media: Wikimedia Commons license-filtering + beat alignment.

Run with:  python -m tests.test_commons   (or: pytest tests/test_commons.py)

Network-free: commons._search and commons._download are stubbed, so no HTTP.
"""
from __future__ import annotations

import tempfile
from pathlib import Path


def test_license_ok_accepts_commercial_rejects_nc_nd():
    from socialbot.media import commons

    ok = [
        {"LicenseShortName": {"value": "CC BY-SA 4.0"}},
        {"LicenseShortName": {"value": "CC0"}},
        {"UsageTerms": {"value": "Public domain"}},
        {"License": {"value": "cc-by-3.0"}},
    ]
    bad = [
        {"LicenseShortName": {"value": "CC BY-NC 2.0"}},     # non-commercial
        {"LicenseShortName": {"value": "CC BY-NC-SA 4.0"}},
        {"LicenseShortName": {"value": "CC BY-ND 4.0"}},     # no-derivatives
        {"UsageTerms": {"value": "All rights reserved"}},
        {},                                                  # unknown -> reject
    ]
    assert all(commons._license_ok(e) for e in ok)
    assert not any(commons._license_ok(e) for e in bad)


def test_fetch_media_keeps_only_licensed_and_builds_credit():
    from socialbot.media import commons

    pages = [
        {  # rejected: non-commercial license
            "title": "File:Bad.jpg",
            "imageinfo": [{
                "mime": "image/jpeg", "thumburl": "http://x/bad.jpg",
                "extmetadata": {"LicenseShortName": {"value": "CC BY-NC 4.0"}},
            }],
        },
        {  # accepted: CC BY-SA, with an HTML-wrapped author
            "title": "File:Voyager probe.jpg",
            "imageinfo": [{
                "mime": "image/jpeg", "thumburl": "http://x/good.jpg",
                "extmetadata": {
                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                    "Artist": {"value": "<a href='#'>Jane Doe</a>"},
                },
            }],
        },
    ]
    commons._search = lambda q, limit=8: pages
    commons._USED_FILE = Path(tempfile.mkdtemp()) / "used_commons.json"

    downloaded: list[str] = []

    def _fake_dl(url, out):
        Path(out).write_bytes(b"\xff\xd8\xff")  # minimal jpeg-ish bytes
        downloaded.append(url)

    commons._download = _fake_dl

    assets = commons.fetch_media(["voyager"], Path(tempfile.mkdtemp()), max_items=3)

    assert len(assets) == 1                      # the NC file was filtered out
    assert downloaded == ["http://x/good.jpg"]   # only the licensed file fetched
    a = assets[0]
    assert "Jane Doe" in a.credit                # HTML stripped from the Artist field
    assert "CC BY-SA 4.0" in a.credit
    assert a.path.exists()


def test_align_beats_consumes_words_in_order():
    from socialbot.media.tts import Word
    from socialbot.pipeline import align_beats
    from socialbot.script import Beat

    words = [Word("a", 0.0, 0.5), Word("b", 0.5, 1.0), Word("c", 1.0, 1.4), Word("d", 1.4, 2.0)]
    beats = [Beat(text="a b", query="q1"), Beat(text="c d", query="q2")]

    spans = align_beats(beats, words)
    assert spans == [(0.0, 1.0), (1.0, 2.0)]
    # no timings -> empty (caller falls back to even cuts)
    assert align_beats(beats, []) == []
    assert align_beats([], words) == []


def test_align_beats_clamps_when_beats_overrun_words():
    from socialbot.media.tts import Word
    from socialbot.pipeline import align_beats
    from socialbot.script import Beat

    words = [Word("only", 0.0, 0.5), Word("two", 0.5, 1.0)]
    beats = [Beat(text="only two three four five", query="q")]  # more words than timings
    spans = align_beats(beats, words)
    assert spans == [(0.0, 1.0)]  # clamps to the last available word


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} test(s) passed.")


if __name__ == "__main__":
    _run_all()
