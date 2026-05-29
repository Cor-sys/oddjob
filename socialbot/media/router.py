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

# Unambiguous space terms (matched as whole words, so "startup" won't hit "star").
# A single one of these is enough to route a topic to NASA footage.
_SPACE_WORDS = {
    "space", "spacecraft", "spaceflight", "spacex", "planet", "planets",
    "exoplanet", "exoplanets", "galaxy", "galaxies", "nebula", "nebulae",
    "cosmic", "cosmos", "telescope", "telescopes",
    "nasa", "esa", "spacewalk", "rocket", "rockets", "satellite", "satellites",
    "asteroid", "asteroids", "comet", "comets", "meteor", "meteorite",
    "lunar", "martian", "astronaut", "astronauts",
    "cosmonaut", "alien", "aliens", "ufo", "ufos", "uap", "extraterrestrial",
    "interstellar", "supernova", "hubble", "jwst", "spaceship",
}
# Ambiguous terms: real space words that also double as product/project names or
# everyday English ("JUPITER" the supercomputer, "solar" power, a sports "star",
# the Marvel "universe"). These route to space ONLY when no clear tech/computing
# context is present — otherwise the planet name is almost certainly a codename.
_AMBIGUOUS_SPACE = {
    "mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune", "pluto",
    "moon", "star", "stars", "stellar", "solar", "orbit", "orbital", "universe",
    "webb",
}
# Tech/computing signals that demote a lone ambiguous token (so "JUPITER
# supercomputer simulates qubits" stays a tech topic, not a Jupiter space topic).
_TECH_WORDS = {
    "quantum", "qubit", "qubits", "supercomputer", "computing", "processor",
    "processors", "chip", "chips", "semiconductor", "gpu", "cpu", "algorithm",
    "algorithms", "software", "hardware", "app", "ai", "ml", "llm", "model",
    "models", "neural", "dataset", "server", "servers", "cloud", "datacenter",
    "startup", "blockchain", "crypto", "robot", "robotics", "transistor",
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
    if any(w.startswith(_SPACE_PREFIXES) for w in words):
        return True
    # Only ambiguous evidence (planet names, "star", "solar", ...): trust it as a
    # space topic unless the story is clearly about tech/computing, where those
    # words are usually codenames rather than the celestial body.
    if (words & _AMBIGUOUS_SPACE) and not (words & _TECH_WORDS):
        return True
    return False


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
