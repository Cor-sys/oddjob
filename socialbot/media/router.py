"""Per-topic visual router.

Picks the right footage source for each story:
  - space / astronomy / aliens -> NASA public-domain media (real, on-topic, free)
  - everything else (AI, tech, general) -> Pexels stock
NASA-routed topics fall back to Pexels if NASA comes up short.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..script import Script
from ..trends import Topic
from . import nasa, stock

# Whole-word space terms (matched against tokens, so "startup" won't hit "star").
_SPACE_WORDS = {
    "space", "spacecraft", "spaceflight", "spacex", "planet", "planets",
    "exoplanet", "exoplanets", "galaxy", "galaxies", "nebula", "nebulae",
    "star", "stars", "stellar", "cosmic", "cosmos", "telescope", "telescopes",
    "nasa", "esa", "spacewalk", "rocket", "rockets", "satellite", "satellites",
    "asteroid", "asteroids", "comet", "comets", "meteor", "meteorite", "moon",
    "lunar", "mars", "martian", "jupiter", "saturn", "venus", "neptune",
    "uranus", "pluto", "solar", "orbit", "orbital", "astronaut", "astronauts",
    "cosmonaut", "alien", "aliens", "ufo", "ufos", "uap", "extraterrestrial",
    "universe", "interstellar", "supernova", "hubble", "webb", "jwst", "spaceship",
}
# Prefix terms (match the start of a token: astronomy/astronomer, etc.).
_SPACE_PREFIXES = ("astronom", "astrophys", "cosmolog")
# Multi-word phrases checked as substrings.
_SPACE_PHRASES = ("black hole", "milky way", "solar system", "outer space",
                  "space telescope", "hot jupiter", "deep space")

# Reliable, beautiful NASA imagery used to top up space topics.
_SPACE_FALLBACKS = ["nebula", "spiral galaxy", "planet", "stars in space", "space telescope"]


def _is_space_topic(topic: Topic, script: Script) -> bool:
    blob = " ".join([
        topic.title, topic.summary, " ".join(topic.keywords),
        " ".join(script.broll_keywords),
    ]).lower()
    if any(phrase in blob for phrase in _SPACE_PHRASES):
        return True
    words = set(re.findall(r"[a-z0-9]+", blob))
    if words & _SPACE_WORDS:
        return True
    return any(w.startswith(_SPACE_PREFIXES) for w in words)


def fetch_visuals(topic: Topic, script: Script, dest_dir: Path, max_items: int = 6) -> list[Path]:
    """Return background media (images and/or clips) for this topic."""
    if _is_space_topic(topic, script):
        print("     [router] space topic -> NASA")
        keywords = script.broll_keywords + _SPACE_FALLBACKS
        media = nasa.fetch_media(keywords, dest_dir / "nasa", max_items)
        if len(media) < max(2, max_items // 2):
            need = max_items - len(media)
            print(f"     [router] NASA thin ({len(media)}) -> topping up with Pexels")
            media += stock.fetch_broll(script.broll_keywords, dest_dir / "pexels", need)
        return media

    print("     [router] non-space topic -> Pexels")
    return stock.fetch_broll(script.broll_keywords, dest_dir / "pexels", max_items)
