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


# On a billed tier the failure mode is transient 503/empty, not minute-window
# rate limits — so retry briefly, then fall back to the next model fast.
_MAX_TRIES = 2
_BACKOFF = (3, 8)


def _model_chain(primary: str) -> list[str]:
    """Ordered, de-duplicated list of models to try: the requested one first,
    then the configured fallback and the other role models as further backups."""
    chain: list[str] = []
    for m in (primary, settings.gemini_fallback_model,
              settings.gemini_model, settings.gemini_calls_model):
        if m and m not in chain:
            chain.append(m)
    return chain


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
                last_err = ValueError("empty response from model")
            if attempt < _MAX_TRIES - 1:
                time.sleep(_BACKOFF[attempt])  # wait out the per-minute limit

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


def grounded(prompt: str, *, system: str | None = None) -> tuple[str, list[str]]:
    """Run a search-grounded generation. Returns (text, source_urls)."""
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        system_instruction=system,
        temperature=0.4,
    )
    # grounded data calls (trends, fact-check) use the cheaper "calls" model
    resp = _generate(prompt, config, model=settings.gemini_calls_model)
    return resp.text or "", _grounding_sources(resp)


def grounded_json(prompt: str, *, system: str | None = None) -> tuple[Any, list[str]]:
    """Search-grounded generation that we coerce into JSON. Returns (obj, sources)."""
    text, sources = grounded(prompt, system=system)
    return _extract_json(text), sources


def json_call(prompt: str, *, schema: dict | None = None, system: str | None = None) -> Any:
    """Structured generation (no search). Returns parsed JSON."""
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
        system_instruction=system,
        temperature=0.7,
    )
    resp = _generate(prompt, config)
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
