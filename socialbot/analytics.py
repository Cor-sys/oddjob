"""The feedback loop: turn real YouTube performance into a strategy the bot obeys.

Every ~2 weeks you export a YouTube Studio CSV and run `cli analytics <file>`.
This module:
  1. ingest()    — parse the CSV (source-agnostic: a header-synonym map copes with
                   Studio's column names/locale differences),
  2. join()      — match each row to the video we posted (by YouTube id stored in
                   the item meta), merge in that video's experiment arm, and append
                   a snapshot line to data/analytics.jsonl (idempotent per day),
  3. synthesize() — aggregate retention by arm dimension, compute selection weights
                   (deterministically, with a small-sample guard), and make ONE
                   Flash call for human-readable directives -> data/strategy.json.

The daily batch then reads strategy.json read-only (0 calls): it reweights topic
selection and nudges the scriptwriter's phrasing — always inside the fact-checked
bounds. Synthesis is the only LLM call here and runs only on `cli analytics`.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone

from . import costs
from .config import DATA_DIR, PUBLISHED_DIR, settings

ANALYTICS_FILE = DATA_DIR / "analytics.jsonl"
STRATEGY_FILE = DATA_DIR / "strategy.json"

# Below this many videos the data is too noisy to trust — weights are clamped
# toward 1.0 (no strong steer) until enough videos accumulate.
_MIN_SAMPLE = 8
_MIN_PER_VALUE = 2
_WEIGHT_LO, _WEIGHT_HI = 0.6, 1.5

_DIMENSIONS = ("topic_cluster", "hook_style", "length_bucket", "voice")

_STRATEGY_SYSTEM = (
    "You are a short-form channel strategist. Given how the channel's own videos "
    "performed, broken down by attribute, you write a few concrete, actionable "
    "directives the scriptwriter can follow. You reply with ONLY the requested JSON."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# ── 1. ingest ────────────────────────────────────────────────────────────────

# target field -> matching rules. video_id is matched exactly (so "Video title"
# never gets mistaken for the id column); the rest match by substring.
_EXACT = {"video_id": {"video", "video id", "video_id", "content", "id"}}
_SUBSTR = {
    "views": ["views"],
    # "Stayed to watch %" is YouTube's Shorts swipe-past-the-hook metric — bounded
    # 0-100 and the truest retention signal. Preferred over "% viewed", which loops
    # inflate past 100 on Shorts (see ingest()).
    "stayed_pct": ["stayed to watch"],
    "avg_view_pct": ["average percentage viewed", "average view percentage", "avg view %", "percentage viewed"],
    "avg_view_seconds": ["average view duration", "avg view duration", "view duration"],
    # The "Content" export omits the average columns but gives watch-time + duration,
    # from which view-duration (and a last-resort % viewed) is derivable.
    "watch_time_hours": ["watch time (hours)", "watch time"],
    "duration": ["duration"],
    "ctr": ["click-through rate", "click through rate", "ctr"],
    "likes": ["likes"],
}


def _norm(s: str) -> str:
    return (s or "").strip().strip('"').lower()


def _map_headers(headers: list[str]) -> dict[str, int]:
    cols: dict[str, int] = {}
    for i, h in enumerate(headers):
        nh = _norm(h)
        if "video_id" not in cols and nh in _EXACT["video_id"] and "title" not in nh:
            cols["video_id"] = i
            continue
        for target, needles in _SUBSTR.items():
            if target in cols:
                continue
            if any(n in nh for n in needles):
                cols[target] = i
                break
    return cols


def _to_float(val: str) -> float:
    try:
        return float(_norm(val).replace("%", "").replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _to_seconds(val: str) -> float:
    v = _norm(val)
    if ":" in v:
        parts = [p for p in v.split(":") if p != ""]
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return 0.0
        secs = 0.0
        for n in nums:
            secs = secs * 60 + n
        return secs
    return _to_float(val)


def ingest(csv_path: str) -> list[dict]:
    """Parse a YouTube Studio CSV into normalized metric rows (one per video).
    Raises RuntimeError listing the detected columns if no video-id column is found."""
    from pathlib import Path

    path = Path(csv_path).expanduser()
    if not path.exists():
        raise RuntimeError(f"analytics CSV not found: {path}")

    text = path.read_text(encoding="utf-8-sig", errors="replace")
    reader = csv.reader(text.splitlines())
    rows = list(reader)
    if not rows:
        raise RuntimeError("analytics CSV is empty")

    headers = rows[0]
    cols = _map_headers(headers)
    if "video_id" not in cols:
        raise RuntimeError(
            "Could not find a video-id column in the CSV. Detected headers: "
            + ", ".join(headers)
        )

    out: list[dict] = []
    for raw in rows[1:]:
        if len(raw) <= cols["video_id"]:
            continue
        vid = _norm(raw[cols["video_id"]])
        # Studio CSVs include a "Total" summary row with no real video id — skip it.
        if not vid or vid in ("total", "totals"):
            continue

        def cell(name: str) -> str:
            i = cols.get(name)
            return raw[i] if i is not None and i < len(raw) else ""

        views = _to_float(cell("views"))
        # Retention signal (0-100). Prefer "Stayed to watch %" (bounded, the real
        # Shorts hook metric); then YouTube's "% viewed" column; last resort, derive
        # it from view-duration / length — but that loops past 100 on Shorts, so cap.
        pct = _to_float(cell("stayed_pct")) or _to_float(cell("avg_view_pct"))
        secs = _to_seconds(cell("avg_view_seconds"))
        if not secs and views:
            secs = _to_float(cell("watch_time_hours")) * 3600.0 / views
        if not pct:
            dur = _to_seconds(cell("duration"))
            if dur and secs:
                pct = min(secs / dur * 100.0, 100.0)

        out.append({
            "video_id": raw[cols["video_id"]].strip(),
            "views": views,
            "avg_view_pct": round(pct, 2),
            "avg_view_seconds": round(secs, 2),
            "ctr": _to_float(cell("ctr")),
            "likes": _to_float(cell("likes")),
        })
    return out


# ── 2. join ──────────────────────────────────────────────────────────────────

def _published_index() -> dict[str, "object"]:
    """Map each posted YouTube video id -> the review item we created for it."""
    from . import review

    index: dict[str, object] = {}
    for it in review.list_items(base=PUBLISHED_DIR):
        yt = (it.meta.get("publish_results", {}) or {}).get("youtube", {}) or {}
        vid = yt.get("id")
        if vid:
            index[vid] = it
    return index


def _read_jsonl(path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def join(rows: list[dict]) -> int:
    """Merge metric rows with the experiment arm of each matching posted video and
    append snapshot records to analytics.jsonl. Idempotent per (video_id, day).
    Returns the number of new records written."""
    index = _published_index()
    existing = _read_jsonl(ANALYTICS_FILE)
    seen = {(r.get("video_id"), r.get("as_of")) for r in existing}
    as_of = _today()

    new: list[dict] = []
    for row in rows:
        vid = row["video_id"]
        item = index.get(vid)
        if not item:
            continue  # a video we didn't post (or not posted from this repo)
        key = (vid, as_of)
        if key in seen:
            continue
        seen.add(key)
        new.append({
            "video_id": vid,
            "item_id": item.id,
            "as_of": as_of,
            "experiment_arm": item.meta.get("experiment_arm", {}),
            "metrics": {
                "views": row["views"],
                "avg_view_pct": row["avg_view_pct"],
                "avg_view_seconds": row["avg_view_seconds"],
                "ctr": row["ctr"],
                "likes": row["likes"],
            },
            "topic_title": item.meta.get("topic_title", ""),
            "posted_at": item.meta.get("updated_at") or item.meta.get("created_at", ""),
        })

    if new:
        try:
            ANALYTICS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with ANALYTICS_FILE.open("a", encoding="utf-8") as fh:
                for r in new:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        except OSError:
            pass
    return len(new)


# ── 3. synthesize ──────────────────────────────────────────────────────────────

def _latest_per_video(records: list[dict]) -> list[dict]:
    """Keep only the most recent snapshot per video (metrics grow over time)."""
    latest: dict[str, dict] = {}
    for r in records:
        vid = r.get("video_id")
        if vid and (vid not in latest or r.get("as_of", "") >= latest[vid].get("as_of", "")):
            latest[vid] = r
    return list(latest.values())


def _aggregate(records: list[dict]) -> dict:
    """Mean retention (%) overall and per arm-dimension value."""
    pcts = [r["metrics"].get("avg_view_pct", 0) for r in records]
    overall = sum(pcts) / len(pcts) if pcts else 0.0
    agg: dict = {"overall_pct": overall, "n": len(records), "dimensions": {}}
    for dim in _DIMENSIONS:
        buckets: dict[str, list[float]] = {}
        for r in records:
            val = str(r.get("experiment_arm", {}).get(dim, "") or "?")
            buckets.setdefault(val, []).append(r["metrics"].get("avg_view_pct", 0))
        agg["dimensions"][dim] = {
            val: {"n": len(v), "mean_pct": round(sum(v) / len(v), 2)}
            for val, v in buckets.items()
        }
    return agg


def _weights(agg: dict) -> dict:
    """Turn per-value mean retention into selection multipliers, clamped, with a
    small-sample guard that pulls weak evidence back toward 1.0 (no steer)."""
    overall = agg["overall_pct"] or 1.0
    sample = agg["n"]
    weights: dict[str, dict] = {}
    for dim, vals in agg["dimensions"].items():
        weights[dim] = {}
        for val, stat in vals.items():
            raw = (stat["mean_pct"] / overall) if overall else 1.0
            raw = max(_WEIGHT_LO, min(_WEIGHT_HI, raw))
            # Trust the signal only with enough data, both overall and per value.
            if sample < _MIN_SAMPLE or stat["n"] < _MIN_PER_VALUE:
                raw = 1.0 + (raw - 1.0) * 0.25     # heavy pull toward neutral
            weights[dim][val] = round(raw, 3)
    return weights


def _directives(agg: dict) -> tuple[list[str], str]:
    """ONE Flash call: human-readable directives from the aggregates. Falls back
    to a generic directive set if the model is unavailable."""
    from .llm import json_call

    summary = {dim: {v: s["mean_pct"] for v, s in vals.items()}
               for dim, vals in agg["dimensions"].items()}
    prompt = f"""Here is how this short-form channel's own videos performed,
as average-percentage-viewed (retention %) broken down by attribute.

SAMPLE SIZE: {agg['n']} videos
OVERALL MEAN RETENTION: {agg['overall_pct']:.1f}%
BY ATTRIBUTE: {json.dumps(summary, ensure_ascii=False)}

Write 3-6 concrete, actionable directives the scriptwriter and topic-picker should
follow next, based ONLY on what this data supports (favor the higher-retention
attribute values; be cautious where the sample is tiny). Keep each directive one
short sentence.

Return ONLY JSON: {{"directives":["..."],"notes":"<=1 sentence caveat"}}"""
    try:
        with costs.track(stage="strategy"):
            data = json_call(prompt, system=_STRATEGY_SYSTEM)
        directives = [str(d).strip() for d in (data.get("directives") or []) if str(d).strip()]
        notes = str(data.get("notes", "")).strip()
        if directives:
            return directives[:6], notes
    except Exception as e:
        print(f"  [strategy] directive synthesis unavailable ({type(e).__name__}); using generic")
    return (
        ["Lead with the single most surprising concrete fact.",
         "Keep each line escalating; cut throat-clearing.",
         "End on a line that loops back to the hook."],
        "Generic directives (insufficient data or model unavailable).",
    )


def synthesize() -> dict:
    """Build data/strategy.json from the accumulated analytics. Returns the strategy."""
    records = _latest_per_video(_read_jsonl(ANALYTICS_FILE))
    if not records:
        raise RuntimeError("No analytics records yet — run `cli analytics <csv>` first.")
    agg = _aggregate(records)
    weights = _weights(agg)
    directives, notes = _directives(agg)
    strategy = {
        "version": 1,
        "updated_at": _now(),
        "sample_size": agg["n"],
        "weights": weights,
        "directives": directives,
        # Adaptive posting bar (judge-score units). 0 = no learned bar yet, so the
        # batch uses settings.post_score_floor. Once enough videos carry both a
        # judge score and real retention (we now persist the per-dimension judge
        # subscores on each item), this can be raised to the score below which the
        # channel's own videos reliably under-retain. Until that correlation exists
        # we don't invent a number — the scales differ (retention % vs quality 0-100).
        "post_floor": 0,
        "notes": notes,
    }
    try:
        STRATEGY_FILE.write_text(json.dumps(strategy, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return strategy


def load_strategy() -> dict:
    """Read data/strategy.json (or {} if there isn't one yet). Used read-only by
    the daily batch — never triggers an LLM call."""
    if not STRATEGY_FILE.exists():
        return {}
    try:
        data = json.loads(STRATEGY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def cluster_weight(strategy: dict, cluster: str) -> float:
    """Selection multiplier for a topic cluster (1.0 if unknown / no strategy)."""
    try:
        return float(strategy.get("weights", {}).get("topic_cluster", {}).get(cluster, 1.0))
    except (TypeError, ValueError):
        return 1.0
