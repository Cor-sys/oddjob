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
    "scripts will perform best as YouTube Shorts. You judge on hook strength, "
    "escalation, payoff, clarity and rewatchability. You reply with ONLY the "
    "requested JSON — never prose."
)


@dataclass
class Finalist:
    topic: Topic
    dossier: Dossier
    script: Script
    seconds: int = 0
    concept_score: float = _NEUTRAL
    judge_score: float = 0.0
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
    """Mine `n` fresh, on-niche concepts (one grounded discover call)."""
    n = n or settings.concepts_n
    topics = discover(count=n + 2, niche=niche)
    return topic_history.filter_new(topics)[:n]


def _dedup_topics(topics: list[Topic]) -> list[Topic]:
    """Drop near-duplicate titles within a pool (same logic as topic_history)."""
    seen: list[set[str]] = []
    out: list[Topic] = []
    for t in topics:
        fp = topic_history._fingerprint(t.title)
        if fp and any(topic_history._similar(fp, prev) for prev in seen):
            continue
        seen.append(fp)
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
    prompt = f"""Score each candidate 0-100 on its potential as a 30-45 second,
retention-optimized mini-documentary Short.

Reward: a specific, surprising, under-told angle; strong visual potential; enough
real depth to sustain 40 seconds; evergreen curiosity; and genuine audience
demand (the search-demand hint reflects real YouTube autocomplete interest).
Penalize: broad/generic headlines, thin or purely-opinion topics, anything hard
to show on screen.

CANDIDATES:
{listing}

Return ONLY JSON covering EVERY candidate:
{{"scores":[{{"index":<1-based number above>,"score":<0-100>,"reason":"<=12 words"}}]}}"""

    with costs.track(stage="batch_score"):
        data = json_call(prompt, system=_SCORE_SYSTEM, model=settings.gemini_calls_model)
    scores = _parse_scores(data, len(pool))

    # Bias SELECTION by the learned strategy's topic-cluster weights (Phase 5),
    # but persist the RAW scores to the bank so the steer is re-applied each run.
    ranked_raw = list(zip(pool, scores))
    strategy = analytics.load_strategy()

    def _weighted(topic: Topic, score: float) -> float:
        cluster = experiment.classify_cluster(topic.title + " " + " ".join(topic.keywords))
        return score * analytics.cluster_weight(strategy, cluster)

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


def judge(finalists: list[Finalist]) -> list[float]:
    """Rank the finalists in ONE Flash call; returns a score per finalist."""
    if not finalists:
        return []
    blocks = []
    for i, f in enumerate(finalists, 1):
        hook = f.script.hook_candidates[0] if f.script.hook_candidates else f.script.hook_text
        blocks.append(f"[{i}] HOOK: {hook}\nNARRATION: {f.script.narration[:400]}")
    listing = "\n\n".join(blocks)
    prompt = f"""Judge these finished mini-doc scripts on how well they will
perform as YouTube Shorts (hook strength, escalation, payoff/loop, clarity,
rewatchability). Score each 0-100.

SCRIPTS:
{listing}

Return ONLY JSON covering EVERY script:
{{"rankings":[{{"index":<1-based number above>,"score":<0-100>,"reason":"<=12 words"}}]}}"""

    with costs.track(stage="judge"):
        data = json_call(prompt, system=_JUDGE_SYSTEM)
    return _parse_scores(data, len(finalists))


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


# ── the daily batch ──────────────────────────────────────────────────────────

def run_batch(post: int | None = None, *, niche: str | None = None, dry_run: bool = False) -> dict:
    """Run the full tournament. Posts the top `post` winners on staggered native
    YouTube `publishAt` slots, banks the rest as recipes, and tops up any shortfall
    from the reserve. `dry_run` exercises the whole LLM funnel (so you can verify
    the call budget) but renders/uploads/banks nothing.

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
                f.factcheck = factcheck.vet(f.script)
            f.cost = _add_costs(f.cost, frun.as_dict())

        survivors = [f for f in finalists if f.factcheck.verdict != factcheck.REJECTED]
        summary["rejected"] = len(finalists) - len(survivors)
        if not survivors:
            print("[batch] all finalists were rejected by fact-check.")
            summary["calls"], summary["cost_usd"] = run.llm_calls, round(run.cost_usd, 6)
            return summary

        for f, s in zip(survivors, judge(survivors)):
            f.judge_score = s
        survivors.sort(key=lambda f: f.judge_score, reverse=True)

        postable = [f for f in survivors if _publishable(f)]
        to_post = postable[:post]
        chosen = {id(f) for f in to_post}
        to_bank = [f for f in survivors
                   if id(f) not in chosen and f.judge_score >= settings.bank_score_floor]

        summary["calls"] = run.llm_calls
        summary["cost_usd"] = round(run.cost_usd, 6)

        print(f"\n[batch] funnel: {len(topics)} -> {len(scored)} -> {len(finalists)} finalists "
              f"-> {len(survivors)} survived fact-check")
        print(f"[batch] plan: post {len(to_post)}, bank {len(to_bank)}  "
              f"(used {run.llm_calls} LLM calls, ~${run.cost_usd:.4f})")
        for f in to_post:
            print(f"   POST  [{f.judge_score:5.1f}] {f.script.on_screen_title}")
        for f in to_bank:
            print(f"   BANK  [{f.judge_score:5.1f}] {f.script.on_screen_title}")

        if dry_run:
            summary["posted"] = [f.script.on_screen_title for f in to_post]
            summary["banked"] = [f.script.on_screen_title for f in to_bank]
            print("[batch] DRY RUN — nothing rendered, uploaded, or banked.")
            return summary

        # Render + schedule the winners on staggered native publishAt slots.
        slots = pipeline.next_publish_times(post)
        for f in to_post:
            try:
                item = _materialize(f)
                pipeline.schedule_item(item, slots[len(summary["posted"])])
                summary["posted"].append(item.id)
            except Exception as e:
                print(f"  !! post failed for {f.script.on_screen_title}: {e}")

        for f in to_bank:
            try:
                it = reserve.bank(_finalist_meta(f, status=review.RESERVE))
                summary["banked"].append(it.id)
            except Exception as e:
                print(f"  !! bank failed for {f.script.on_screen_title}: {e}")

        # Top up any shortfall from the reserve bank (0 LLM calls). Over-pull a
        # few extra so we can skip any recipe that isn't auto-publishable (e.g. a
        # banked needs_review) and still fill the open slots.
        shortfall = post - len(summary["posted"])
        if shortfall > 0:
            candidates = reserve.best(shortfall + 3, exclude=set(summary["banked"]))
            if candidates:
                print(f"[batch] {shortfall} open slot(s); trying the reserve bank")
            for recipe in candidates:
                if len(summary["posted"]) >= post:
                    break
                if not pipeline._publishable_verdict(recipe.meta):
                    continue  # can't auto-publish this one — leave it banked
                try:
                    reserve.render_reserve(recipe.id, publish_at=slots[len(summary["posted"])])
                    summary["posted"].append(recipe.id)
                except Exception as e:
                    print(f"  !! reserve fill failed for {recipe.id}: {e}")

        # Remember what we actually used so future runs don't repeat these stories.
        topic_history.remember([f.topic for f in to_post] + [f.topic for f in to_bank])
        print(f"[batch] done: posted {len(summary['posted'])}, banked {len(summary['banked'])}.")
        return summary
