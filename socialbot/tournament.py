"""Best-of-N tournament: the daily batch that turns the free-tier request budget
into the strongest possible 3 posts.

The funnel (sizes are config knobs; defaults fit ~20 Flash + ~20 Flash-Lite/day):

    mine ~15 concepts            (1 grounded discover call)
      -> score, keep best ~10    (1 Flash-Lite call)
      -> develop each: research + draft script   (~2 calls each)
      -> cull to ~4 finalists    (0 calls — heuristic on research richness)
      -> polish each finalist    (1 Flash call each)
      -> fact-check each         (1 grounded call each — the sacred gate)
      -> judge / rank survivors  (1 Flash call)
      -> post the top `posts_per_day`, bank the rest as recipes

Fact-check is never bypassed: a REJECTED finalist is neither posted nor banked.
Banking additionally requires a judge score at/above `bank_score_floor`. If the
funnel yields fewer postable winners than slots, the shortfall is filled from the
reserve bank (re-rendered recipes, 0 LLM calls).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import analytics, costs, demand, experiment, factcheck, pipeline, reserve, review, topic_bank, topic_history
from .config import settings
from .llm import json_call
from .research import Dossier, research
from .script import (Script, _facts_block, _script_from_data, write_script)
from .script import _SYSTEM as _SCRIPT_SYSTEM
from .trends import Topic, discover

_NEUTRAL = 50.0

_SCORE_SYSTEM = (
    "You are a ruthless short-form content strategist for a channel about "
    "frontier science and real mysteries. You can tell, fast, which ideas will "
    "hold attention for 40 seconds and which are generic filler. You reply with "
    "ONLY the requested JSON — never prose."
)

_JUDGE_SYSTEM = (
    "You are a sharp short-form video editor choosing which finished mini-doc "
    "scripts will perform best as YouTube Shorts. You grade execution against a "
    "fixed, calibrated rubric — not a vibe — and reply with ONLY the requested "
    "JSON, never prose."
)

# Shared anchor scale so a score means the same thing every run (calibration is the
# whole point — an unanchored 0-100 drifts run-to-run and a quality floor can't bite).
_ANCHORS = (
    "Score each dimension 0-100 on this ABSOLUTE scale — be calibrated, don't grade "
    "on a curve:\n"
    "  90-100 = exceptional, best-in-class\n"
    "  70-85  = strong, clearly works\n"
    "  55-69  = solid but unremarkable\n"
    "  40-54  = weak / generic\n"
    "  0-39   = broken or a non-starter\n"
    "Most real candidates land 45-75; reserve 85+ for genuinely exceptional."
)

# Stage 1 (concept) grades the IDEA's editorial potential; showability/demand are
# handled deterministically (footage_affinity x cluster_weight + the demand hint),
# so they're deliberately NOT in this rubric (no double-counting).
_CONCEPT_DIMS = {"hook_potential": 0.40, "depth": 0.35, "payoff": 0.25}
# Stage 2 (judge) grades the finished script's EXECUTION against the same bar the
# writer was told to hit (see script._SYSTEM).
_JUDGE_DIMS = {"hook": 0.30, "escalation": 0.25, "specificity": 0.20,
               "payoff_loop": 0.15, "filmability": 0.10}


@dataclass
class Finalist:
    topic: Topic
    dossier: Dossier
    script: Script
    seconds: int = 0
    concept_score: float = _NEUTRAL
    judge_score: float = 0.0
    judge_subscores: dict = field(default_factory=dict)
    factcheck: "factcheck.FactCheck | None" = None
    cost: dict = field(default_factory=lambda: _ZERO_COST.copy())


_ZERO_COST = {"estimated_cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "llm_calls": 0}


def _add_costs(a: dict, b: dict) -> dict:
    a, b = a or {}, b or {}
    return {
        "estimated_cost_usd": round(float(a.get("estimated_cost_usd", 0)) + float(b.get("estimated_cost_usd", 0)), 6),
        "input_tokens": int(a.get("input_tokens", 0)) + int(b.get("input_tokens", 0)),
        "output_tokens": int(a.get("output_tokens", 0)) + int(b.get("output_tokens", 0)),
        "llm_calls": int(a.get("llm_calls", 0)) + int(b.get("llm_calls", 0)),
    }


def _directives_block() -> str:
    """Learned directives (Phase 5) injected into BOTH graders so they reward what
    actually retains on this channel. Empty until analytics have been ingested —
    a no-op today, live the moment `cli analytics` writes a strategy."""
    try:
        directives = analytics.load_strategy().get("directives") or []
    except Exception:
        return ""
    if not directives:
        return ""
    lines = "\n".join(f"  - {d}" for d in directives[:6])
    return ("\nWHAT RETAINS ON THIS CHANNEL (learned from our own analytics — reward, "
            "all else equal, candidates that do these):\n" + lines + "\n")


def _grade_items(data: object) -> list:
    """Pull the per-candidate dicts out of a grading reply, tolerating the shapes
    models actually return: {"scores":[...]}, {"rankings":[...]}, a bare list, or a
    {"1":{...},"2":{...}} / {"1":80} index map."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        items = data.get("scores") or data.get("rankings") or data.get("items")
        if items:
            return items
        out = []
        for k, v in data.items():
            try:
                idx = int(k)
            except (TypeError, ValueError):
                continue
            if isinstance(v, dict):
                v = {**v, "index": v.get("index", idx)}
            else:
                v = {"index": idx, "score": v}
            out.append(v)
        return out
    return []


def _parse_graded(data: object, count: int, weights: dict[str, float]) -> list[tuple[float, dict]]:
    """Parse an anchored grading reply into per-candidate (total, subscores), aligned
    to the 1-based indices we asked the model to grade.

    The TOTAL is computed in code as the weighted mean of the per-dimension subscores
    (the model's freeform 'score', if any, is ignored) so the scale stays consistent
    run-to-run. Missing dimensions fall back to neutral; a reply with no subscores at
    all falls back to a flat 'score' field, else neutral."""
    results: list[tuple[float, dict]] = [(_NEUTRAL, {}) for _ in range(count)]
    for it in _grade_items(data):
        if not isinstance(it, dict):
            continue
        try:
            i = int(it.get("index")) - 1
        except (TypeError, ValueError):
            continue
        if not (0 <= i < count):
            continue
        subs: dict[str, float] = {}
        for dim in weights:
            v = it.get(dim)
            if v is None:
                continue
            try:
                subs[dim] = max(0.0, min(100.0, float(v)))
            except (TypeError, ValueError):
                pass
        if subs:
            total = round(sum(subs.get(d, _NEUTRAL) * w for d, w in weights.items()), 1)
        else:
            try:
                total = float(it.get("score", _NEUTRAL))
            except (TypeError, ValueError):
                total = _NEUTRAL
        results[i] = (total, subs)
    return results


def _parse_scores(data: object, count: int) -> list[float]:
    """Coerce a scoring/ranking model reply into a list of `count` floats aligned
    to the 1-based indices we asked it to score. Missing -> neutral."""
    scores = [_NEUTRAL] * count
    items: list = []
    if isinstance(data, dict):
        items = data.get("scores") or data.get("rankings") or []
        if not items:  # tolerate {"1": 80, "2": 60}
            for k, v in data.items():
                try:
                    i = int(k) - 1
                    if 0 <= i < count:
                        scores[i] = float(v)
                except (TypeError, ValueError):
                    pass
            return scores
    elif isinstance(data, list):
        items = data
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            i = int(it.get("index")) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= i < count:
            try:
                scores[i] = float(it.get("score", _NEUTRAL))
            except (TypeError, ValueError):
                pass
    return scores


# ── funnel stages ─────────────────────────────────────────────────────────────

def concepts(n: int | None = None, *, niche: str | None = None) -> list[Topic]:
    """Mine `n` fresh, on-niche concepts (one grounded discover call). Recently-
    covered story titles are fed into discovery so the model avoids them up front."""
    n = n or settings.concepts_n
    topics = discover(count=n + 2, niche=niche, avoid=topic_history.recent_titles())
    return topic_history.filter_new(topics)[:n]


def _dedup_topics(topics: list[Topic]) -> list[Topic]:
    """Drop near-duplicate stories within a pool (same story-key logic as
    topic_history's cross-run dedup, so a fresh concept and a carried-over bank
    concept about the same event collapse to one)."""
    seen: list[set[str]] = []
    out: list[Topic] = []
    for t in topics:
        key = topic_history._topic_key(t)
        if key and any(
            topic_history._similar(key, prev, threshold=topic_history._STORY_SIMILARITY)
            for prev in seen
        ):
            continue
        seen.append(key)
        out.append(t)
    return out


def score_concepts(topics: list[Topic], keep: int | None = None) -> list[tuple[Topic, float]]:
    """Score concepts in ONE Flash-Lite call; return the best `keep` as
    (topic, score) pairs, highest first.

    Phase 4: the scoring pool also pulls the top unused concepts from the topic
    bank (strong ideas carried over from past days), each pool member is enriched
    with a free YouTube-autocomplete demand signal + niche-filtered, and the full
    scored set is persisted back to the bank (developed ones marked used)."""
    keep = keep or settings.develop_n

    # Merge in strong unused concepts carried over from previous batches.
    try:
        carried = topic_bank.top(settings.bank_merge_n, unused_only=True)
        if carried:
            print(f"  [bank] merging {len(carried)} carried-over concept(s)")
    except Exception as e:
        print(f"  [bank] unavailable ({type(e).__name__}); using fresh concepts only")
        carried = []
    pool = _dedup_topics(list(topics) + carried)
    if not pool:
        return []

    # Free demand signal + on-niche filter (1 Flash-Lite call, HTTP otherwise free).
    try:
        pool = demand.enrich(pool) or pool
    except Exception as e:
        print(f"  [demand] enrich failed ({type(e).__name__}); scoring raw pool")

    listing = "\n".join(
        f"{i}. {t.title} — {t.summary} (search demand: {int(getattr(t, 'demand', 0) or 0)}/100)"
        for i, t in enumerate(pool, 1)
    )
    prompt = f"""Grade each candidate as a 30-45 second retention-optimized mini-documentary
Short. Judge the IDEA's potential ONLY — the script isn't written yet, so don't grade
prose; grade whether a great script COULD be built from this.

{_ANCHORS}

Score each candidate on these dimensions (0-100 each):
  - hook_potential: is there a specific, scroll-stopping, curiosity-gap angle a first
    sentence could open on? Broad/generic headlines and thin opinion score low.
  - depth: enough concrete, specific material (real numbers, names, events) to sustain
    ~40 seconds of escalating reveals — not one thin fact stretched across the clip?
  - payoff: is there a satisfying "wait, what?" resolution the video can land on?

The search-demand hint reflects real YouTube autocomplete interest — let it nudge ties.
{_directives_block()}
CANDIDATES:
{listing}

Return ONLY JSON covering EVERY candidate by its number:
{{"scores":[{{"index":<n>,"hook_potential":<0-100>,"depth":<0-100>,"payoff":<0-100>,"reason":"<=12 words"}}]}}"""

    with costs.track(stage="batch_score"):
        data = json_call(prompt, system=_SCORE_SYSTEM, model=settings.gemini_calls_model)
    scores = [total for total, _subs in _parse_graded(data, len(pool), _CONCEPT_DIMS)]

    # Bias SELECTION by the learned strategy's topic-cluster weights (Phase 5),
    # but persist the RAW scores to the bank so the steer is re-applied each run.
    ranked_raw = list(zip(pool, scores))
    strategy = analytics.load_strategy()

    def _weighted(topic: Topic, score: float) -> float:
        cluster = experiment.classify_cluster(topic.title + " " + " ".join(topic.keywords))
        # Strategy bias (learned) x footage availability (can we show it well?).
        return (score
                * analytics.cluster_weight(strategy, cluster)
                * experiment.footage_affinity(topic))

    ranked = sorted(ranked_raw, key=lambda ts: _weighted(*ts), reverse=True)

    # Persist the whole scored set; mark the ones we'll develop as used.
    try:
        topic_bank.add_or_update(ranked_raw, source="batch")
        topic_bank.mark_used([t for t, _ in ranked[:keep]])
    except Exception as e:
        print(f"  [bank] persist failed ({type(e).__name__}); continuing")

    return ranked[:keep]


def develop(topic: Topic, *, seconds: int | None = None) -> Finalist:
    """Research a concept and draft its mini-doc script (~2 LLM calls). When
    `seconds` is unset, the length is jittered across the range (biased toward the
    strategy's favored length bucket) so length becomes a real experiment arm."""
    if seconds is None:
        seconds = experiment.jittered_seconds(analytics.load_strategy())
    dossier = research(topic)
    script = write_script(topic, seconds, dossier=dossier)
    return Finalist(topic=topic, dossier=dossier, script=script, seconds=seconds)


def _richness(f: Finalist) -> float:
    """0-call heuristic for the develop-stage cull: concept score plus signals
    that the draft has real documentary substance."""
    d, s = f.dossier, f.script
    score = f.concept_score
    score += min(len(d.facts), 10) * 2          # +20 for a deep fact sheet
    score += min(len(d.entities), 5)            # +5  for filmable subjects
    score += 5 if d.surprising_angle else 0
    words = len(s.narration.split())
    lo = int(settings.clip_seconds_min * 2.3)
    hi = int(settings.clip_seconds_max * 2.9)
    score += 5 if lo <= words <= hi else 0      # within a sane length budget
    score += 5 if len(s.shot_list) >= 3 else 0  # has a real shot list
    return score


def polish(f: Finalist) -> None:
    """Critique-and-rewrite one finalist for retention (1 Flash call), staying
    strictly within the verified facts. Mutates `f.script` in place; keeps the
    original if the rewrite comes back empty."""
    facts_block, _ = _facts_block(f.topic, f.dossier)
    target_words = int(f.seconds * 2.6)
    hooks = "\n".join(f"- {h}" for h in (f.script.hook_candidates or [f.script.hook_text]))
    prompt = f"""Polish this near-final mini-documentary script for maximum
retention. Critique it silently, then return an IMPROVED version.

{facts_block}

CURRENT NARRATION:
{f.script.narration}

CANDIDATE OPENINGS (use the strongest as the first line, or write a better one
built ONLY on the facts above):
{hooks}

Improve it: make the first sentence stop the scroll in ~3 seconds; cut every
filler word; ensure each line escalates; make the final line loop back into the
hook. Stay STRICTLY within the facts above — invent nothing. Keep it ~{target_words} words.

Return ONLY a JSON object with these keys:
  "narration", "hook_candidates" (3 alternative openings, best first),
  "hook_text" (<=7 words), "on_screen_title" (<=6 words), "description",
  "hashtags" (3-5, no # symbol, no generic spam tags),
  "shot_list" (ordered beats whose "text" values concatenate to the narration;
   each {{"text","query","kind": "space"|"entity"|"stock"}})."""

    with costs.track(stage="polish"):
        data = json_call(prompt, system=_SCRIPT_SYSTEM)
    new = _script_from_data(f.topic, data)
    if new.narration:
        f.script = new


def judge(finalists: list[Finalist]) -> list[tuple[float, dict]]:
    """Grade the finished scripts in ONE Flash call against the anchored EXECUTION
    rubric; returns (weighted_total, subscores) per finalist, aligned to input order.
    The total is computed in code from the subscores so the scale stays consistent."""
    if not finalists:
        return []
    blocks = []
    for i, f in enumerate(finalists, 1):
        hook = f.script.hook_candidates[0] if f.script.hook_candidates else f.script.hook_text
        shots = ", ".join(b.query for b in f.script.shot_list[:8]) or "(none)"
        # FULL narration — the payoff/loop is the LAST line, so truncating it blinds
        # the judge to the payoff_loop dimension (it scores ~0). A 30-45s script is
        # only ~90 words; cap generously just to bound a pathological outlier.
        blocks.append(
            f"[{i}] HOOK: {hook}\nNARRATION: {f.script.narration[:1500]}\nSHOTS: {shots}"
        )
    listing = "\n\n".join(blocks)
    prompt = f"""Grade these finished mini-doc scripts on EXECUTION as YouTube Shorts.

{_ANCHORS}

Score each script on these dimensions (0-100 each):
  - hook: does the FIRST sentence stop the scroll in ~3 seconds with a concrete,
    surprising fact? Penalize setup, scene-setting, or restating the title.
  - escalation: does every line deliver a NEW concrete fact and build? Penalize
    restating, padding, and empty connective filler.
  - specificity: real numbers/names/measurements vs vague scale-words ("colossal",
    "mysterious", "incredible")?
  - payoff_loop: does the LAST line land the point AND loop back into the hook so a
    replay feels seamless?
  - filmability: do the SHOTS name concrete subjects free archives can actually show
    (named people/places/craft, or showable generics) vs abstract concepts?
{_directives_block()}
SCRIPTS:
{listing}

Return ONLY JSON covering EVERY script by its number:
{{"rankings":[{{"index":<n>,"hook":<0-100>,"escalation":<0-100>,"specificity":<0-100>,"payoff_loop":<0-100>,"filmability":<0-100>,"reason":"<=12 words"}}]}}"""

    with costs.track(stage="judge"):
        data = json_call(prompt, system=_JUDGE_SYSTEM)
    return _parse_graded(data, len(finalists), _JUDGE_DIMS)


# ── meta / publish gate ─────────────────────────────────────────────────────────

def _finalist_meta(f: Finalist, *, status: str) -> dict:
    fc = f.factcheck.to_dict() if f.factcheck else {
        "verdict": factcheck.NEEDS_REVIEW, "summary": "not fact-checked"
    }
    return {
        "topic": f.topic.to_dict(),
        "research": f.dossier.to_dict(),
        "topic_title": f.topic.title,
        "on_screen_title": f.script.on_screen_title,
        "script": f.script.to_dict(),
        "factcheck": fc,
        "clip_seconds": f.seconds,
        "concept_score": round(f.concept_score, 1),
        "judge_score": round(f.judge_score, 1),
        # Per-dimension execution grades — kept so the analytics loop can later
        # correlate which grading dimensions actually predict retention.
        "judge_subscores": {k: round(v, 1) for k, v in (f.judge_subscores or {}).items()},
        "generation_cost": f.cost,
        # Partial arm (voice is filled in at render); lets banked recipes carry
        # their experiment attribution even before they're rendered.
        "experiment_arm": experiment.assign_arm(f.topic, f.script, seconds=f.seconds),
        "status": status,
    }


def _publishable(f: Finalist) -> bool:
    """Same auto-publish gate the rest of the pipeline uses: a clean 'ok', or a
    'needs_review' on inherently-unverifiable (speculative) subject matter."""
    return pipeline._publishable_verdict(_finalist_meta(f, status=review.PENDING))


def _materialize(f: Finalist) -> review.Item:
    """Create a pending review item for a winner and render its clip."""
    item = review.create(_finalist_meta(f, status=review.PENDING))
    pipeline._render(item, f.script, f.topic)
    item.meta["generation_cost"] = f.cost
    item.save()
    return item


def _upload_private(item: review.Item) -> None:
    """Upload a winner to YouTube as PRIVATE with no publishAt — it stays private
    for manual review instead of auto-publishing. Used by `batch --no-schedule`."""
    from .publish import publish_item

    review.approve(item.id)
    fresh = review.get(item.id)
    print(f"  -> uploading {item.id} PRIVATE (no auto-publish)")
    publish_item(fresh, targets=("youtube",))  # no publish_at -> private, unscheduled


def _fmt_subs(subs: dict) -> str:
    """Compact one-line view of the judge subscores, e.g. '(hook 90 esca 80 ...)',
    so a batch run shows WHY a script scored what it did."""
    if not subs:
        return ""
    return "  (" + " ".join(f"{k[:4]} {int(round(v))}" for k, v in subs.items()) + ")"


def _effective_post_floor(strategy: dict | None) -> float:
    """The posting quality bar (anchored judge-score units): the configured floor,
    raised by any learned floor the analytics loop has set (strategy.post_floor).
    A fresh winner must clear this to be posted."""
    base = settings.post_score_floor
    try:
        learned = float((strategy or {}).get("post_floor", 0) or 0)
    except (TypeError, ValueError):
        learned = 0.0
    return max(base, learned)


# ── the daily batch ──────────────────────────────────────────────────────────

def run_batch(post: int | None = None, *, niche: str | None = None, dry_run: bool = False,
              schedule: bool = True) -> dict:
    """Run the full tournament. Posts the top `post` winners on staggered native
    YouTube `publishAt` slots, banks the rest as recipes, and tops up any shortfall
    from the reserve. `dry_run` exercises the whole LLM funnel (so you can verify
    the call budget) but renders/uploads/banks nothing. With `schedule=False` the
    winners are uploaded PRIVATE with no publishAt (for manual review) instead of
    being auto-scheduled, and the reserve top-up is skipped.

    NOTE: a dry run still consumes the day's LLM requests — it is a budget probe,
    not a free preview."""
    post = post or settings.posts_per_day
    summary: dict = {
        "dry_run": dry_run, "developed": 0, "rejected": 0,
        "posted": [], "banked": [], "calls": 0, "cost_usd": 0.0,
    }

    with costs.track(stage="batch") as run:
        strategy = analytics.load_strategy()
        if strategy.get("directives"):
            print(f"[batch] applying strategy (sample {strategy.get('sample_size', '?')}): "
                  + " | ".join(strategy["directives"][:3]))
        print(f"[batch] mining {settings.concepts_n} concepts"
              + (f" (niche: {niche})" if niche else "") + " ...")
        topics = concepts(settings.concepts_n, niche=niche)
        if not topics:
            print("[batch] no fresh concepts found — nothing to do.")
            return summary

        scored = score_concepts(topics, keep=settings.develop_n)
        print(f"[batch] scored {len(topics)} concepts -> developing top {len(scored)}")

        finalists: list[Finalist] = []
        for topic, cscore in scored:
            if run.llm_calls >= settings.batch_call_ceiling:
                print(f"[batch] call ceiling ({settings.batch_call_ceiling}) reached; "
                      "stopping development to protect the daily budget.")
                break
            try:
                with costs.track(topic=topic.title) as frun:
                    f = develop(topic)
                f.concept_score = cscore
                f.cost = frun.as_dict()
                finalists.append(f)
                print(f"  developed: {topic.title}")
            except Exception as e:  # one bad concept shouldn't kill the batch
                print(f"  !! develop failed for {topic.title}: {e}")

        if not finalists:
            print("[batch] nothing developed.")
            summary["calls"], summary["cost_usd"] = run.llm_calls, round(run.cost_usd, 6)
            return summary

        finalists.sort(key=_richness, reverse=True)
        finalists = finalists[:settings.finalists_n]
        summary["developed"] = len(finalists)
        print(f"[batch] polishing + fact-checking {len(finalists)} finalists")
        for f in finalists:
            with costs.track(topic=f.topic.title) as frun:
                polish(f)
                f.script, f.factcheck = factcheck.vet_and_revise(
                    f.script, f.topic, dossier=f.dossier, seconds=f.seconds)
            f.cost = _add_costs(f.cost, frun.as_dict())

        survivors = [f for f in finalists if f.factcheck.verdict != factcheck.REJECTED]
        summary["rejected"] = len(finalists) - len(survivors)
        if not survivors:
            print("[batch] all finalists were rejected by fact-check.")
            summary["calls"], summary["cost_usd"] = run.llm_calls, round(run.cost_usd, 6)
            return summary

        for f, (total, subs) in zip(survivors, judge(survivors)):
            f.judge_score = total
            f.judge_subscores = subs
        survivors.sort(key=lambda f: f.judge_score, reverse=True)

        # Quality floor: a fresh winner must clear the bar to be POSTED. Below it we
        # don't ship filler — we fill from the reserve or post fewer.
        floor = _effective_post_floor(strategy)
        publishable = [f for f in survivors if _publishable(f)]   # fact-check gate
        postable = [f for f in publishable if f.judge_score >= floor]
        to_post = postable[:post]
        chosen = {id(f) for f in to_post}
        to_bank = [f for f in survivors
                   if id(f) not in chosen and f.judge_score >= settings.bank_score_floor]
        blocked = [f for f in publishable if f.judge_score < floor]
        # Manual-review mode (--no-schedule): if nothing clears the bar, still surface
        # the single best survivor for review, clearly flagged.
        below_floor_review = (publishable[0] if publishable and not postable
                              and not schedule else None)

        summary["calls"] = run.llm_calls
        summary["cost_usd"] = round(run.cost_usd, 6)

        print(f"\n[batch] funnel: {len(topics)} -> {len(scored)} -> {len(finalists)} finalists "
              f"-> {len(survivors)} survived fact-check")
        print(f"[batch] plan: post {len(to_post)}, bank {len(to_bank)}  "
              f"(quality floor {floor:.0f}; used {run.llm_calls} LLM calls, ~${run.cost_usd:.4f})")
        for f in to_post:
            print(f"   POST  [{f.judge_score:5.1f}] {f.script.on_screen_title}{_fmt_subs(f.judge_subscores)}")
        for f in to_bank:
            print(f"   BANK  [{f.judge_score:5.1f}] {f.script.on_screen_title}{_fmt_subs(f.judge_subscores)}")
        for f in blocked:
            print(f"   below [{f.judge_score:5.1f}] {f.script.on_screen_title}"
                  f"{_fmt_subs(f.judge_subscores)} (under floor {floor:.0f})")

        if dry_run:
            summary["posted"] = [f.script.on_screen_title for f in to_post]
            summary["banked"] = [f.script.on_screen_title for f in to_bank]
            print("[batch] DRY RUN — nothing rendered, uploaded, or banked.")
            return summary

        # Render the winners, then either schedule them (native publishAt) or
        # upload them PRIVATE for review (schedule=False).
        slots = pipeline.next_publish_times(post) if schedule else []
        for f in to_post:
            try:
                item = _materialize(f)
                if schedule:
                    pipeline.schedule_item(item, slots[len(summary["posted"])])
                else:
                    _upload_private(item)
                summary["posted"].append(item.id)
            except Exception as e:
                print(f"  !! post failed for {f.script.on_screen_title}: {e}")

        # Manual-review mode with nothing above the bar: upload the single best
        # survivor for REVIEW ONLY (flagged) so you can still see what the funnel
        # produced and judge it yourself.
        if below_floor_review is not None and not summary["posted"]:
            f = below_floor_review
            print(f"  -> nothing cleared the quality floor ({floor:.0f}); uploading the "
                  f"best [{f.judge_score:.1f}] for REVIEW ONLY: {f.script.on_screen_title}")
            try:
                item = _materialize(f)
                _upload_private(item)
                summary["posted"].append(item.id)
                summary["below_floor_review"] = item.id
            except Exception as e:
                print(f"  !! review upload failed for {f.script.on_screen_title}: {e}")

        # Record posted stories now (before the reserve top-up) so the reserve
        # fill's coverage check won't re-air a story this batch just posted.
        topic_history.remember([f.topic for f in to_post])

        for f in to_bank:
            try:
                it = reserve.bank(_finalist_meta(f, status=review.RESERVE))
                summary["banked"].append(it.id)
            except Exception as e:
                print(f"  !! bank failed for {f.script.on_screen_title}: {e}")
        # Banked recipes haven't aired and won't hit the publish-time record hook,
        # so remember them here too.
        topic_history.remember([f.topic for f in to_bank])

        # Top up any shortfall from the reserve bank (0 LLM calls). Over-pull a
        # few extra so we can skip any recipe that isn't auto-publishable (e.g. a
        # banked needs_review) and still fill the open slots.
        shortfall = post - len(summary["posted"])
        if shortfall > 0 and schedule:
            candidates = reserve.best(shortfall + 3, exclude=set(summary["banked"]))
            if candidates:
                print(f"[batch] {shortfall} open slot(s); trying the reserve bank")
            for recipe in candidates:
                if len(summary["posted"]) >= post:
                    break
                # The reserve is the cadence net of pre-vetted runners-up: require the
                # recipe to still clear the BANK floor (the strict post floor is for
                # fresh winners; a vetted runner-up is the intended gap-filler).
                try:
                    rscore = float(recipe.meta.get("judge_score", 0) or 0)
                except (TypeError, ValueError):
                    rscore = 0.0
                if rscore < settings.bank_score_floor:
                    continue
                if not pipeline._publishable_verdict(recipe.meta):
                    continue  # can't auto-publish this one — leave it banked
                try:
                    reserve.render_reserve(recipe.id, publish_at=slots[len(summary["posted"])])
                    summary["posted"].append(recipe.id)
                except Exception as e:
                    print(f"  !! reserve fill failed for {recipe.id}: {e}")

        # Post fewer, not filler: flag any slots we deliberately left empty.
        unfilled = post - len(summary["posted"])
        if unfilled > 0 and schedule:
            print(f"[batch] {unfilled} slot(s) left unfilled - nothing cleared the quality "
                  f"floor ({floor:.0f}) and the reserve couldn't fill them; posting fewer "
                  "rather than filler.")

        print(f"[batch] done: posted {len(summary['posted'])}, banked {len(summary['banked'])}.")
        return summary
