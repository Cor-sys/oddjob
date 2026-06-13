"""Tests for the coverage/dedup foundation: story-key identity, the self-healing
artifact scan, the publish-time record, and the discovery avoid-list.

Run with:  python -m tests.test_topic_history   (or: pytest tests/test_topic_history.py)

Network-free: the published/pending/reserve dir constants and the cache file are
redirected to tempdirs, so no real data/ is read or written.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class T:
    """A minimal Topic-like (what filter_new / _topic_key read)."""
    title: str
    keywords: list = field(default_factory=list)


def _isolate():
    """Point topic_history's three queue dirs + cache file at fresh tempdirs."""
    import socialbot.topic_history as th

    tmp = Path(tempfile.mkdtemp())
    th.PUBLISHED_DIR = tmp / "published"
    th.PENDING_DIR = tmp / "pending"
    th.RESERVE_DIR = tmp / "reserve"
    for d in (th.PUBLISHED_DIR, th.PENDING_DIR, th.RESERVE_DIR):
        d.mkdir(parents=True, exist_ok=True)
    th._USED_FILE = tmp / "used_topics.json"
    th._reset_scan_cache()
    return th, tmp


def _write_meta(base: Path, name: str, title: str, keywords: list, status: str = "published"):
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    meta = {"topic_title": title, "topic": {"title": title, "keywords": keywords}, "status": status}
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


# Real ISS keyword vocab (from the two duplicate videos that prompted this work).
_ISS_KW_A = ["International Space Station", "ISS air leak", "SpaceX Dragon capsule", "astronauts sheltering"]
_ISS_KW_B = ["International Space Station", "ISS air leak", "astronaut safe haven", "ISS module repair"]


def test_fingerprint_unchanged_and_set_typed():
    th, _ = _isolate()
    fp = th._fingerprint("ISS Air Leak Worsens, Crew Shelters")
    assert isinstance(fp, set)                    # demand.py does set algebra on this
    assert fp == {"iss", "air", "leak", "worsens", "crew", "shelters"}
    assert fp & {"iss"} == {"iss"}                # supports & (guards demand.py)


def test_story_key_catches_reworded_story_title_only_misses():
    th, _ = _isolate()
    # Same ISS saga, fully reworded title, realistically-overlapping keywords (the
    # model picks similar entities for the same event — the real duplicate shared
    # these verbatim).
    a = T("ISS Air Leak Worsens, Crew Shelters",
          ["International Space Station", "ISS air leak", "SpaceX Dragon capsule", "astronauts sheltering"])
    b = T("Russia's Risky Saw Repair",
          ["International Space Station", "ISS air leak", "SpaceX Dragon capsule", "ISS module repair"])
    # story-key (title + keywords) collides at the story threshold...
    assert th._similar(th._topic_key(a), th._topic_key(b), threshold=th._STORY_SIMILARITY)
    # ...whereas the old title-only fingerprint does NOT — the bug we're fixing.
    assert not th._similar(th._fingerprint(a.title), th._fingerprint(b.title))


def test_story_key_does_not_overdedup_distinct_space_topics():
    th, _ = _isolate()
    pairs = [
        (T("Jupiter's Shrinking Great Red Spot", ["Jupiter", "Great Red Spot", "gas giant storm"]),
         T("Saturn's Rings Are Vanishing", ["Saturn", "planetary rings", "ice particles"])),
        (T("Voyager 1 Leaves Solar System", ["Voyager 1", "interstellar space", "NASA probe"]),
         T("Artemis III Names Moon Crew", ["Artemis III", "Moon landing", "astronaut crew"])),
        (T("Black Hole Devours a Star", ["black hole", "accretion disk", "gravity"]),
         T("Neutron Star Collision Detected", ["neutron star", "kilonova", "gravitational waves"])),
    ]
    for x, y in pairs:
        assert not th._similar(th._topic_key(x), th._topic_key(y), threshold=th._STORY_SIMILARITY), \
            f"over-deduped {x.title!r} vs {y.title!r}"


def test_scan_unions_artifacts_drops_reworded_duplicate():
    """The regression test: with an EMPTY cache, a published ISS artifact alone is
    enough for filter_new to drop a reworded ISS topic."""
    th, _ = _isolate()
    _write_meta(th.PUBLISHED_DIR, "20260610-232701_iss-repair", "ISS Air Leak Worsens, Crew Shelters", _ISS_KW_A)
    th._reset_scan_cache()

    reworded = T("ISS Leak Crisis: Risky Repair", ["International Space Station", "ISS air leak", "astronauts sheltering"])
    fresh_topic = T("Webb Telescope Spots a Rogue Planet", ["James Webb telescope", "rogue planet", "infrared"])

    survivors = th.filter_new([reworded, fresh_topic])
    assert [s.title for s in survivors] == ["Webb Telescope Spots a Rogue Planet"]


def test_cache_wipe_is_harmless():
    """Empty cache + populated published/ → still covered via the scan."""
    th, _ = _isolate()
    _write_meta(th.PUBLISHED_DIR, "20260610-232701_iss", "ISS Air Leak Worsens, Crew Shelters", _ISS_KW_A)
    th._reset_scan_cache()
    assert th._load() == []                        # cache genuinely empty
    probe = {"topic_title": "ISS Leak Crisis",
             "topic": {"keywords": ["International Space Station", "ISS air leak", "SpaceX Dragon capsule"]}}
    assert th.is_covered_meta(probe)


def test_rejected_excluded_from_gate_but_present_in_recent_titles():
    th, _ = _isolate()
    _write_meta(th.PENDING_DIR, "20260101-000000_debunked", "Debunked UFO Sighting",
                ["ufo", "debunked"], status="rejected")
    th._reset_scan_cache()
    # not a hard gate hit (a debunked story may become coverable later)...
    assert not th.is_covered_meta({"topic_title": "Debunked UFO Sighting", "topic": {"keywords": ["ufo", "debunked"]}})
    # ...but still fed to the discover avoid-block so we stop re-mining it.
    assert "Debunked UFO Sighting" in th.recent_titles()


def test_reserve_recipe_does_not_cover_itself():
    """A banked reserve recipe must NOT count as already-aired against itself."""
    th, _ = _isolate()
    _write_meta(th.RESERVE_DIR, "20260101-000000_banked", "Mars Mission Reality Check",
                ["Mars", "Starship", "orbital refueling"], status="reserve")
    th._reset_scan_cache()
    meta = {"topic_title": "Mars Mission Reality Check", "topic": {"keywords": ["Mars", "Starship", "orbital refueling"]}}
    assert not th.is_covered_meta(meta)            # reserve-only -> not aired/queued


def test_memoization_single_disk_walk():
    th, _ = _isolate()
    calls = [0]
    real = th._disk_walk
    try:
        def counting():
            calls[0] += 1
            return real()
        th._disk_walk = counting
        th._reset_scan_cache()
        th._coverage()
        th._coverage()
        assert calls[0] == 1                       # memoized: one disk pass
        th.remember([T("A Brand New Story", ["novel"])])   # resets the memo
        th._coverage()
        assert calls[0] == 2
    finally:
        th._disk_walk = real


def test_recent_titles_recency_cap_and_dedup():
    th, _ = _isolate()
    for i in range(5):
        _write_meta(th.PUBLISHED_DIR, f"2026010{i}-000000_t{i}", f"Title {i}", [f"kw{i}"])
    # a newer folder repeating an older title — must de-dup to one
    _write_meta(th.PUBLISHED_DIR, "20260109-000000_dup", "Title 4", ["kwdup"])
    th._reset_scan_cache()

    assert th.recent_titles(3) == ["Title 4", "Title 3", "Title 2"]   # newest-first, capped
    assert th.recent_titles(30).count("Title 4") == 1                 # de-duped


def test_reconcile_backfills_cache_and_is_idempotent():
    th, _ = _isolate()
    for i, (title, kw) in enumerate([("Alpha Story", ["alpha"]), ("Beta Story", ["beta"]), ("Gamma Story", ["gamma"])]):
        _write_meta(th.PUBLISHED_DIR, f"2026010{i}-000000_x", title, kw)
    th._reset_scan_cache()

    r1 = th.reconcile()
    assert r1["scanned"] == 3 and r1["cache_size"] == 3
    r2 = th.reconcile()
    assert r2["cache_size"] == 3                    # idempotent — no growth


def test_empty_key_topic_passes_through():
    th, _ = _isolate()
    assert th.filter_new([T("", [])]) == [T("", [])]   # nothing to match on -> kept


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} test(s) passed.")


if __name__ == "__main__":
    _run_all()
