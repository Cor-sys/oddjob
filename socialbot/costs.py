"""Spend ledger: record what each run actually costs.

The bot leans on free/quota services (YouTube Data API, edge-tts, Pexels, NASA),
so the only line item that can cost real money is the Gemini API used for trend
discovery, scripting, and fact-checking. This module:

  - captures token usage from every Gemini call and estimates its dollar cost
    from a published price table (overridable via the GEMINI_PRICES env var),
  - records each YouTube upload (quota units consumed; $0 — the API is free),
  - appends one JSON line per event to data/costs.jsonl, and
  - aggregates the ledger into a human/JSON summary (see `summary()`).

Attribution is contextual: wrap a stage in `with costs.track(stage="script"):`
and every Gemini call inside it is tagged accordingly. Frames nest, so a
pipeline can wrap a whole video in `with costs.track(topic=...) as run:` and
read `run.cost_usd` afterwards to learn what that one video cost to make.

Costs are ESTIMATES based on list prices — actual billing (especially on the
free tier, where it's $0) is authoritative in the Google Cloud / AI Studio
console. Recording is best-effort and never raises into the pipeline.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR

COSTS_FILE = DATA_DIR / "costs.jsonl"

# YouTube Data API: a videos.insert call costs ~100 quota units against the
# free 10,000 units/day allowance (changed from 1,600 on Dec 4 2025). No money.
YOUTUBE_UPLOAD_QUOTA_UNITS = 100

# Published Gemini list prices in USD per 1,000,000 tokens (input, output).
# These are estimates for the paid tier; free-tier usage bills at $0. Override
# with GEMINI_PRICES, e.g. GEMINI_PRICES='{"gemini-2.5-flash":{"input":0.3,"output":2.5}}'
_DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
}


def _prices() -> dict[str, dict[str, float]]:
    prices = {k: dict(v) for k, v in _DEFAULT_PRICES.items()}
    raw = os.getenv("GEMINI_PRICES", "").strip()
    if raw:
        try:
            for model, p in json.loads(raw).items():
                prices[model] = {
                    "input": float(p.get("input", 0.0)),
                    "output": float(p.get("output", 0.0)),
                }
        except (ValueError, AttributeError):
            pass  # malformed override — fall back to defaults
    return prices


def _price_for(model: str) -> dict[str, float]:
    """Resolve a model name to its price entry, tolerating version suffixes
    (e.g. 'gemini-2.5-flash-002'). Prefers the longest matching key so
    'flash-lite' wins over 'flash'."""
    prices = _prices()
    if model in prices:
        return prices[model]
    best: dict[str, float] | None = None
    best_len = -1
    for key, p in prices.items():
        if model.startswith(key) and len(key) > best_len:
            best, best_len = p, len(key)
    return best or {"input": 0.0, "output": 0.0}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    p = _price_for(model)
    return input_tokens / 1e6 * p["input"] + output_tokens / 1e6 * p["output"]


# ── contextual attribution ──────────────────────────────────────────────────

@dataclass
class Run:
    """An accumulator for the cost of everything recorded within a `track()`
    block. Read its fields after the block to learn what that scope cost."""
    fields: dict[str, Any] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    llm_calls: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "estimated_cost_usd": round(self.cost_usd, 6),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "llm_calls": self.llm_calls,
        }


_stack: contextvars.ContextVar[tuple[Run, ...]] = contextvars.ContextVar(
    "costs_stack", default=()
)


@contextlib.contextmanager
def track(**fields: Any):
    """Tag Gemini calls made in this block with `fields` (e.g. stage, topic,
    item_id). Yields a `Run` that accumulates the block's token/cost totals.
    Frames nest and inherit their parent's fields."""
    parent = _stack.get()
    merged: dict[str, Any] = {}
    for frame in parent:
        merged.update(frame.fields)
    merged.update({k: v for k, v in fields.items() if v is not None})
    run = Run(fields=merged)
    token = _stack.set(parent + (run,))
    try:
        yield run
    finally:
        _stack.reset(token)


def _current_fields() -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for frame in _stack.get():
        fields.update(frame.fields)
    return fields


def _append(entry: dict[str, Any]) -> None:
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    try:
        COSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with COSTS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # never let bookkeeping break the pipeline


# ── recording ───────────────────────────────────────────────────────────────

def record_llm(model: str, response: Any) -> None:
    """Record token usage + estimated cost for one Gemini response.
    Best-effort: any failure (missing usage metadata, IO error) is swallowed."""
    try:
        usage = getattr(response, "usage_metadata", None)
        input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        # Thinking models bill their reasoning tokens as output too.
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0) + int(
            getattr(usage, "thoughts_token_count", 0) or 0
        )
    except (TypeError, ValueError):
        return
    if not input_tokens and not output_tokens:
        return

    cost = estimate_cost(model, input_tokens, output_tokens)
    for frame in _stack.get():
        frame.input_tokens += input_tokens
        frame.output_tokens += output_tokens
        frame.cost_usd += cost
        frame.llm_calls += 1

    _append({
        "type": "llm",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": round(cost, 6),
        **_current_fields(),
    })


def record_youtube_upload(video_id: str, *, file_size: int | None = None) -> None:
    """Record a successful YouTube upload. The Data API is free, so the dollar
    cost is 0; we track the quota units consumed instead."""
    _append({
        "type": "youtube_upload",
        "platform": "youtube",
        "video_id": video_id,
        "quota_units": YOUTUBE_UPLOAD_QUOTA_UNITS,
        "estimated_cost_usd": 0.0,
        "file_size_bytes": file_size,
        **_current_fields(),
    })


# ── reporting ────────────────────────────────────────────────────────────────

def _read_ledger() -> list[dict[str, Any]]:
    if not COSTS_FILE.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in COSTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def summary() -> dict[str, Any]:
    """Aggregate the ledger into totals by stage, model, and platform, plus a
    dedicated YouTube-posting view."""
    entries = _read_ledger()
    llm = [e for e in entries if e.get("type") == "llm"]
    uploads = [e for e in entries if e.get("type") == "youtube_upload"]

    def _sum(items: list[dict], key: str) -> float:
        return sum(float(e.get(key, 0) or 0) for e in items)

    by_stage: dict[str, float] = {}
    by_model: dict[str, dict[str, float]] = {}
    for e in llm:
        stage = e.get("stage", "unknown")
        by_stage[stage] = by_stage.get(stage, 0.0) + float(e.get("estimated_cost_usd", 0) or 0)
        m = by_model.setdefault(e.get("model", "unknown"), {"cost_usd": 0.0, "calls": 0})
        m["cost_usd"] += float(e.get("estimated_cost_usd", 0) or 0)
        m["calls"] += 1

    youtube_uploads = [e for e in uploads if e.get("platform") == "youtube"]
    return {
        "total_estimated_cost_usd": round(_sum(llm, "estimated_cost_usd"), 6),
        "total_input_tokens": int(_sum(llm, "input_tokens")),
        "total_output_tokens": int(_sum(llm, "output_tokens")),
        "llm_calls": len(llm),
        "by_stage": {k: round(v, 6) for k, v in sorted(by_stage.items())},
        "by_model": {
            k: {"cost_usd": round(v["cost_usd"], 6), "calls": int(v["calls"])}
            for k, v in sorted(by_model.items())
        },
        "youtube": {
            "uploads": len(youtube_uploads),
            "quota_units_used": int(_sum(youtube_uploads, "quota_units")),
            "posting_cost_usd": round(_sum(youtube_uploads, "estimated_cost_usd"), 6),
        },
    }
