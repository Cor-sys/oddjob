"""Search-grounded fact-check / vetting pass over a generated script."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import costs
from .llm import grounded_json
from .script import Script

_SYSTEM = (
    "You are a strict fact-checker. You verify each factual claim against current "
    "web sources. You are conservative: if a claim cannot be confirmed, you flag it. "
    "You never approve content containing unverifiable or false claims. "
    "CRITICAL: you ALWAYS reply with ONLY the requested JSON object — never refuse "
    "in prose, never add commentary. If claims are unsupported, express that via "
    "the verdict ('needs_review' or 'rejected') and the claim statuses, not by "
    "declining to answer."
)

# verdicts
OK = "ok"                    # safe to publish
NEEDS_REVIEW = "needs_review"  # human should look before publishing
REJECTED = "rejected"        # contains likely-false claims, do not publish


@dataclass
class ClaimCheck:
    claim: str
    status: str   # supported | unverified | contradicted
    note: str = ""


@dataclass
class FactCheck:
    verdict: str
    summary: str
    claims: list[ClaimCheck] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @property
    def publishable(self) -> bool:
        return self.verdict == OK

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "summary": self.summary,
            "claims": [c.__dict__ for c in self.claims],
            "sources": self.sources,
        }


def vet(script: Script) -> FactCheck:
    prompt = f"""Fact-check this short video narration against current web sources.

NARRATION:
\"\"\"{script.narration}\"\"\"

Steps:
  1. Extract the distinct factual claims.
  2. For each, search and mark status as "supported", "unverified", or
     "contradicted", with a one-line note.
  3. Choose an overall "verdict":
       - "ok"            : all material claims supported
       - "needs_review"  : some claims unverified but nothing contradicted
       - "rejected"      : any material claim is contradicted/false

Return ONLY a JSON object:
{{
  "verdict": "...",
  "summary": "one sentence overall assessment",
  "claims": [{{"claim": "...", "status": "...", "note": "..."}}]
}}"""

    try:
        with costs.track(stage="factcheck"):
            data, sources = grounded_json(prompt, system=_SYSTEM)
    except Exception as e:
        # Model refused / returned prose / transient error — hold for safety
        # rather than crashing the topic. needs_review means "do not auto-publish".
        print(f"     [factcheck] inconclusive ({type(e).__name__}); holding as needs_review")
        return FactCheck(
            verdict=NEEDS_REVIEW,
            summary="Fact-check did not return a usable result; held (not auto-published).",
            sources=[],
        )
    if not isinstance(data, dict):
        data = {}

    verdict = str(data.get("verdict", NEEDS_REVIEW)).strip().lower()
    if verdict not in (OK, NEEDS_REVIEW, REJECTED):
        verdict = NEEDS_REVIEW

    claims = [
        ClaimCheck(
            claim=str(c.get("claim", "")).strip(),
            status=str(c.get("status", "unverified")).strip().lower(),
            note=str(c.get("note", "")).strip(),
        )
        for c in data.get("claims", [])
        if isinstance(c, dict) and c.get("claim")
    ]

    return FactCheck(
        verdict=verdict,
        summary=str(data.get("summary", "")).strip(),
        claims=claims,
        sources=sources,
    )
