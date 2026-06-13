"""Thin wrapper around the Google GenAI (Gemini) SDK.

Provides one shared client plus two call styles:
  - grounded():  Gemini + Google Search, returns text (+ source URLs)
  - json():      structured JSON output parsed into a Python object
"""
from __future__ import annotations

import json
import re
import time
from functools import lru_cache
from typing import Any

from google import genai
from google.genai import errors, types

from .config import settings


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    settings.require_gemini()
    return genai.Client(api_key=settings.gemini_api_key)


# Flash-Lite soft-fails heavy grounded calls with an empty STOP response (finish
# _reason=STOP, parts=0): it finishes the search/tool phase and stops WITHOUT ever
# emitting the answer. Measured ~67% empty on the real research prompt with default
# (near-zero) thinking, dropping to ~17% once a thinking budget is given (see
# _GROUNDED_THINK_BUDGET) — the model needs room to reason through a two-pass
# grounded JSON ask before it can produce it. The residual empties are genuine
# transient blips: an empty response bills ~0 output tokens (output is the expensive
# part), so a couple of quick re-attempts on the cheap model are nearly free and
# keep grounded calls on Flash-Lite instead of falling back to the 6x-pricier Flash.
# Keep retries few and the backoff short: ride out a blip, otherwise drop to the
# (reliable) Flash fallback fast rather than stalling.
_MAX_TRIES = 3
_BACKOFF = (1, 3)  # seconds between retries; clamped if shorter than _MAX_TRIES-1

# Thinking budget for grounded calls on Flash-Lite. Without it the model STOPs
# before answering ~2/3 of the time; 512 cuts that to ~1/6 (the rest the retry/
# fallback above absorbs). Cheap insurance: ~800 thinking tokens on the cheapest
# model beats falling back to Flash, and a healthy Flash-Lite call now succeeds
# first-try instead of wasting three retries before the fallback.
_GROUNDED_THINK_BUDGET = 512


def _model_chain(primary: str) -> list[str]:
    """Ordered, de-duplicated list of models to try: the requested one first,
    then the configured fallback and the other role models as further backups."""
    chain: list[str] = []
    for m in (primary, settings.gemini_fallback_model,
              settings.gemini_model, settings.gemini_calls_model):
        if m and m not in chain:
            chain.append(m)
    return chain


def _empty_reason(resp: Any) -> str:
    """Best-effort explanation for why ``resp.text`` came back blank — finish
    reason, a safety block, or no candidates at all. Used only for diagnostics
    so an 'empty response' tells us *why*. Never raises."""
    bits: list[str] = []
    try:
        block = getattr(getattr(resp, "prompt_feedback", None), "block_reason", None)
        if block:
            bits.append(f"prompt_blocked={getattr(block, 'name', block)}")
    except Exception:
        pass
    try:
        cands = resp.candidates or []
        if not cands:
            bits.append("no_candidates")
        for c in cands[:1]:
            fr = getattr(c, "finish_reason", None)
            if fr is not None:
                bits.append(f"finish_reason={getattr(fr, 'name', fr)}")
            blocked = [
                getattr(getattr(r, "category", None), "name", str(getattr(r, "category", "?")))
                for r in (getattr(c, "safety_ratings", None) or [])
                if getattr(r, "blocked", False)
            ]
            if blocked:
                bits.append(f"blocked_safety={','.join(blocked)}")
            parts = getattr(getattr(c, "content", None), "parts", None) or []
            bits.append(f"parts={len(parts)}")
    except Exception:
        pass
    return ", ".join(bits) or "no detail available"


def _generate(contents: str, config: types.GenerateContentConfig, *, model: str | None = None):
    """Generate content with resilience to free-tier flakiness.

    `model` selects the primary model (defaults to the quality model). On failure
    we walk the fallback chain.
    - 429 (quota/rate exhausted): fall back to the next model immediately —
      retrying the same model is pointless, the quota won't refill in seconds.
    - 503 (overloaded) or an empty response: transient, retry with short backoff.
    - any other client error (bad key/request): raise right away.
    """
    models = _model_chain(model or settings.gemini_model)

    last_err: Exception | None = None
    for idx, model in enumerate(models):
        if idx > 0:
            reason = f" ({type(last_err).__name__}: {getattr(last_err, 'code', None) or last_err})"
            print(f"  [llm] {models[idx - 1]} unavailable{reason}; trying {model}")
        for attempt in range(_MAX_TRIES):
            try:
                resp = _client().models.generate_content(
                    model=model, contents=contents, config=config
                )
            except errors.ClientError as e:
                last_err = e
                if getattr(e, "code", None) == 429:
                    break  # exhausted — go straight to the fallback model
                raise
            except errors.ServerError as e:
                last_err = e
            else:
                if (resp.text or "").strip():
                    from . import costs
                    costs.record_llm(model, resp)
                    return resp
                last_err = ValueError(f"empty response [{_empty_reason(resp)}]")
            if attempt < _MAX_TRIES - 1:
                time.sleep(_BACKOFF[min(attempt, len(_BACKOFF) - 1)])  # ride out the load blip

    raise last_err or RuntimeError("Gemini returned no usable response")


def _extract_json(text: str) -> Any:
    """Parse JSON from a model reply that may be wrapped in ``` fences or have
    trailing prose/citations (common with grounded responses)."""
    text = text.strip()
    # strip code fences
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Decode the first valid JSON value starting at the first { or [, ignoring
    # any trailing text (raw_decode handles the "Extra data" case cleanly).
    decoder = json.JSONDecoder()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if starts:
        try:
            obj, _ = decoder.raw_decode(text[min(starts):])
            return obj
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Model did not return valid JSON:\n{text[:500]}")


def grounded(prompt: str, *, system: str | None = None,
             model: str | None = None) -> tuple[str, list[str]]:
    """Run a search-grounded generation. Returns (text, source_urls).

    `model` picks the primary model. Defaults to the cheap "calls" model
    (Flash-Lite) so the high-volume grounded calls (fact-check) stay on the
    roomier free-tier lane. Pass settings.gemini_model (Flash) for a heavy
    grounded ask that Flash-Lite soft-fails too often — e.g. trend discovery,
    which empty-STOPs and falls back to Flash anyway (see _GROUNDED_THINK_BUDGET);
    routing it to Flash up front skips the wasted empty retries."""
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        system_instruction=system,
        temperature=0.4,
        # give Flash-Lite room to reason or it STOPs before answering — see
        # _GROUNDED_THINK_BUDGET. Harmless on Flash (it thinks by default anyway).
        thinking_config=types.ThinkingConfig(thinking_budget=_GROUNDED_THINK_BUDGET),
    )
    resp = _generate(prompt, config, model=model or settings.gemini_calls_model)
    return resp.text or "", _grounding_sources(resp)


def grounded_json(prompt: str, *, system: str | None = None,
                  model: str | None = None) -> tuple[Any, list[str]]:
    """Search-grounded generation that we coerce into JSON. Returns (obj, sources)."""
    text, sources = grounded(prompt, system=system, model=model)
    return _extract_json(text), sources


def json_call(prompt: str, *, schema: dict | None = None, system: str | None = None,
              model: str | None = None) -> Any:
    """Structured generation (no search). Returns parsed JSON.

    `model` picks the primary model (defaults to the quality model); pass
    `settings.gemini_calls_model` for cheap, mechanical JSON work so the daily
    request budget stays split across the two free-tier model lanes."""
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
        system_instruction=system,
        temperature=0.7,
    )
    resp = _generate(prompt, config, model=model)
    return _extract_json(resp.text or "")


def _grounding_sources(resp: Any) -> list[str]:
    urls: list[str] = []
    try:
        for cand in resp.candidates or []:
            meta = getattr(cand, "grounding_metadata", None)
            for chunk in getattr(meta, "grounding_chunks", None) or []:
                web = getattr(chunk, "web", None)
                if web and getattr(web, "uri", None):
                    urls.append(web.uri)
    except Exception:
        pass
    # de-dupe, preserve order
    return list(dict.fromkeys(urls))
