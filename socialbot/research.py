"""Deep-dive research: expand a Topic into a sourced fact sheet (a Dossier).

This is the v2 step that gives mini-doc scripts real depth. One grounded Gemini
call (the cheaper "calls" model, via grounded_json) runs a two-pass *prompt*:
first establish the verified facts, then dig for the single most surprising,
specific, concrete angle. Nothing here invents anything — every fact must be
supported by the live search results, and factcheck.vet() still vets the
finished script before it can publish.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import costs
from .llm import grounded_json
from .trends import Topic

_SYSTEM = (
    "You are a meticulous documentary researcher. You gather only verifiable, "
    "concrete facts from the live search results — never speculation, never "
    "invented numbers, names, or dates. Your job is to surface the SPECIFIC, "
    "surprising detail a great short documentary is built on, not a bland summary."
)


@dataclass
class Dossier:
    facts: list[str] = field(default_factory=list)        # verified standalone facts
    surprising_angle: str = ""                            # the single most counterintuitive true detail
    specifics: list[str] = field(default_factory=list)    # concrete numbers/names/dates/places
    entities: list[str] = field(default_factory=list)     # named filmable subjects (for footage routing)
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "facts": self.facts,
            "surprising_angle": self.surprising_angle,
            "specifics": self.specifics,
            "entities": self.entities,
            "sources": self.sources,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "Dossier":
        d = d or {}
        return cls(
            facts=[str(x).strip() for x in d.get("facts", []) if str(x).strip()],
            surprising_angle=str(d.get("surprising_angle", "")).strip(),
            specifics=[str(x).strip() for x in d.get("specifics", []) if str(x).strip()],
            entities=[str(x).strip() for x in d.get("entities", []) if str(x).strip()],
            sources=[str(x).strip() for x in d.get("sources", []) if str(x).strip()],
        )

    @property
    def is_thin(self) -> bool:
        """Too little verified material to build a real deep-dive on — callers
        fall back to the topic summary when this is True."""
        return len(self.facts) < 3


def research(topic: Topic) -> Dossier:
    """Expand a Topic into a sourced Dossier via one grounded, two-pass call."""
    prompt = f"""Research this topic for a 30-45 second mini-documentary.

TOPIC: {topic.title}
WHAT WE KNOW: {topic.summary}

Work in two passes, then return JSON:
  PASS 1 - Establish the verified facts. Using live web search, gather the
    concrete, specific, currently-true facts: real numbers, names, dates,
    places, measurements, quotes. Discard anything you cannot verify in the
    search results.
  PASS 2 - Find the angle. From those facts, identify the SINGLE most
    surprising, counterintuitive, or under-told detail — the thing that makes a
    viewer go "wait, what?" — and the concrete specifics that prove it.

Return ONLY a JSON object:
  "facts": 6-10 short, standalone, verified factual statements (most interesting first)
  "surprising_angle": one sentence — the single most surprising true detail
  "specifics": 4-8 concrete data points (numbers, names, dates, places) drawn from the facts
  "entities": 3-6 named, filmable subjects (specific people, places, objects, missions, machines) footage could show
No prose, no markdown."""

    with costs.track(stage="research"):
        data, sources = grounded_json(prompt, system=_SYSTEM)
    dossier = Dossier.from_dict(data if isinstance(data, dict) else {})
    # Merge the call's grounding sources with any the model echoed, order-stable.
    dossier.sources = list(dict.fromkeys([*dossier.sources, *sources]))
    return dossier
