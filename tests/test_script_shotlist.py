"""Tests for the v2 mini-doc script + research dossier (Phase 1).

Run with:  python -m tests.test_script_shotlist   (or: pytest tests/test_script_shotlist.py)

Network-free: the Gemini call in write_script is stubbed by replacing the
module-level json_call, so these exercise the shot-list/round-trip/fallback
logic without touching the SDK.
"""
from __future__ import annotations

import re

_norm = lambda t: re.sub(r"\s+", " ", t).strip().lower()

_NARRATION = (
    "A dead star spins seven hundred times a second. Its gravity bends light "
    "around it. Nothing that drifts too close ever escapes. Which is why it "
    "still spins seven hundred times a second."
)

_FAKE = {
    "narration": _NARRATION,
    "hook_candidates": [
        "A dead star spins seven hundred times a second.",
        "This city-sized star spins 700 times a second.",
        "Seven hundred rotations a second — from a dead star.",
    ],
    "hook_text": "700 spins a second",
    "on_screen_title": "The Fastest Dead Star",
    "description": "How a pulsar bends reality on a city-sized scale.",
    "hashtags": ["#space", "pulsar", "astronomy"],
    "shot_list": [
        {"text": "A dead star spins seven hundred times a second.", "query": "pulsar neutron star", "kind": "space"},
        {"text": "Its gravity bends light around it.", "query": "gravitational lensing", "kind": "space"},
        {"text": "Nothing that drifts too close ever escapes.", "query": "black hole accretion disk", "kind": "space"},
        {"text": "Which is why it still spins seven hundred times a second.", "query": "spinning pulsar animation", "kind": "space"},
    ],
}


def _topic():
    from socialbot.trends import Topic
    return Topic(title="Fastest known pulsar", summary="A pulsar spins very fast.", keywords=["pulsar", "neutron star"])


def test_shotlist_reconstructs_narration_and_keywords():
    from socialbot import script as s

    s.json_call = lambda *a, **k: _FAKE  # stub the network
    sc = s.write_script(_topic(), 38)

    # the beats, concatenated, reproduce the narration (whitespace-insensitive)
    joined = " ".join(b.text for b in sc.shot_list)
    assert _norm(joined) == _norm(_NARRATION)
    # broll_keywords mirror the beat queries (router back-compat)
    assert sc.broll_keywords == [b.query for b in sc.shot_list]
    # hashtags are stripped of '#' and capped
    assert "#" not in "".join(sc.hashtags)
    assert len(sc.hook_candidates) >= 2


def test_script_dict_roundtrips():
    from socialbot import script as s

    s.json_call = lambda *a, **k: _FAKE
    sc = s.write_script(_topic(), 38)
    sc2 = s.Script.from_dict(sc.to_dict())

    assert sc2.narration == sc.narration
    assert sc2.hook_candidates == sc.hook_candidates
    assert [b.to_dict() for b in sc2.shot_list] == [b.to_dict() for b in sc.shot_list]


def test_fallback_beats_when_model_omits_shotlist():
    from socialbot import script as s

    s.json_call = lambda *a, **k: {
        "narration": "First sentence here. Second one follows. Third closes it.",
        "hook_candidates": ["First sentence here."],
        # no shot_list, no broll_keywords -> must fall back to sentence beats
    }
    sc = s.write_script(_topic(), 38)
    assert len(sc.shot_list) == 3                       # one beat per sentence
    assert all(b.query for b in sc.shot_list)           # every beat has a query
    assert sc.broll_keywords == list(_topic().keywords)  # fell back to topic keywords


def test_dossier_roundtrip_and_thinness():
    from socialbot.research import Dossier

    d = Dossier(
        facts=["a", "b", "c"], surprising_angle="x",
        specifics=["1 km"], entities=["Voyager 1"], sources=["http://example.com"],
    )
    d2 = Dossier.from_dict(d.to_dict())
    assert d2.facts == d.facts and d2.entities == d.entities
    assert d2.is_thin is False
    # <3 facts is "thin" -> callers fall back to the topic summary
    assert Dossier.from_dict({}).is_thin is True
    assert Dossier.from_dict({"facts": ["only", "two"]}).is_thin is True


def test_thin_dossier_falls_back_to_summary():
    from socialbot import script as s

    captured = {}

    def _capture(prompt, *a, **k):
        captured["prompt"] = prompt
        return _FAKE

    s.json_call = _capture
    from socialbot.research import Dossier
    s.write_script(_topic(), 38, dossier=Dossier())  # empty/thin dossier
    # with a thin dossier the prompt must carry the topic summary, not "VERIFIED FACTS"
    assert "A pulsar spins very fast." in captured["prompt"]
    assert "VERIFIED FACTS" not in captured["prompt"]


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} test(s) passed.")


if __name__ == "__main__":
    _run_all()
