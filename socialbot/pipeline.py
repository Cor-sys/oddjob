"""Orchestrates: trends -> script -> fact-check -> media -> review queue.

Also hosts `make_promo` (promo mode): build a post from your OWN content — a
song, a product, a website — using your audio/visuals and a clickable link,
instead of an AI-narrated news clip.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import costs, factcheck, review, topic_history
from .config import settings
from .media import stock
from .media.assemble import assemble, trim_audio
from .media.captions import build_ass
from .media.router import fetch_visuals
from .media.tts import _probe_duration, pick_voice, synthesize
from .research import research
from .script import Script, write_script
from .trends import Topic, discover

# YouTube Shorts cap; promo audio is trimmed to this unless --seconds overrides.
_SHORTS_MAX = 60
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def generate_from_topic(topic: Topic, *, seconds: int | None = None, build_video: bool = True) -> review.Item:
    """Run one topic through the full pipeline, returning a queued review Item."""
    seconds = seconds or settings.clip_seconds
    # Tag every Gemini call below with this topic so its spend rolls up per-video.
    with costs.track(topic=topic.title) as run:
        print(f"  -> researching: {topic.title}")
        dossier = research(topic)
        print(f"  -> scripting: {topic.title}")
        script = write_script(topic, seconds, dossier=dossier)

        print("  -> fact-checking...")
        fc = factcheck.vet(script)
        print(f"     verdict={fc.verdict} ({fc.summary})")

        meta = {
            "topic": topic.to_dict(),
            "research": dossier.to_dict(),
            "topic_title": topic.title,
            "on_screen_title": script.on_screen_title,
            "script": script.to_dict(),
            "factcheck": fc.to_dict(),
            "clip_seconds": seconds,
            # What the Gemini calls for this video cost (estimate, list prices).
            "generation_cost": run.as_dict(),
        }

        # Don't waste compute rendering video for content the checker rejected.
        if fc.verdict == factcheck.REJECTED:
            meta["status"] = review.REJECTED
            meta["reject_reason"] = f"fact-check: {fc.summary}"
            item = review.create(meta)
            print(f"  -> REJECTED by fact-check, saved {item.id}")
            return item

        item = review.create(meta)
        if build_video:
            _render(item, script, topic)

        # Refresh the stamped cost so it includes every call made for this video.
        item.meta["generation_cost"] = run.as_dict()
        item.save()
        print(f"  -> queued for review: {item.id} (gen cost ~${run.cost_usd:.4f})")
        return item


def align_beats(shot_list, words) -> list[tuple[float, float]]:
    """Map each shot-list beat to a (start, end) time span using the TTS word
    timings, consuming words in order. Returns [] when there are no timings
    (caller then falls back to even cuts)."""
    if not words or not shot_list:
        return []
    spans: list[tuple[float, float]] = []
    n = len(words)
    idx = 0
    for beat in shot_list:
        wc = max(1, len(beat.text.split()))
        s_i = min(idx, n - 1)
        e_i = min(idx + wc - 1, n - 1)
        spans.append((words[s_i].start, words[e_i].end))
        idx += wc
    return spans


def _render(item: review.Item, script: Script, topic: Topic) -> None:
    work = item.dir / "work"
    work.mkdir(parents=True, exist_ok=True)

    # Reuse a pre-assigned voice (a reserve recipe / experiment arm may have
    # fixed one) so a re-render sounds identical; otherwise rotate the pool.
    voice = item.meta.get("voice") or pick_voice()
    print(f"  -> voiceover ({voice})...")
    vo = synthesize(script.narration, item.dir / "voice.mp3", voice=voice)
    item.meta["voice"] = voice

    # Tag this video's experiment arm so analytics can attribute retention to the
    # topic cluster / hook style / length / voice (Phase 5 feedback loop).
    from . import experiment
    item.meta["experiment_arm"] = experiment.assign_arm(
        topic, script, voice=voice, seconds=item.meta.get("clip_seconds"),
    )

    print("  -> captions...")
    # No opening title/hook card — the video starts straight into the footage with
    # the narration captions rolling from t=0 (no "intro" overlay).
    ass = build_ass(vo.words, item.dir / "captions.ass")

    print("  -> visuals...")
    beat_paths, credits = fetch_visuals(topic, script, work)
    if not beat_paths:
        print("     (no footage available — using gradient background)")

    # Cut footage to the narration beats when we have per-beat footage + timings.
    beats = None
    if beat_paths and script.shot_list:
        spans = align_beats(script.shot_list, vo.words)
        if spans:
            beats = [(s, e, p) for (s, e), p in zip(spans, beat_paths)]

    print("  -> assembling...")
    clip = assemble(
        audio_path=vo.audio_path,
        ass_path=ass,
        broll_paths=beat_paths,
        out_path=item.dir / "clip.mp4",
        duration=vo.duration,
        work_dir=work,
        beats=beats,
    )

    item.meta["clip"] = clip.name
    item.meta["duration"] = round(vo.duration, 2)
    item.meta["used_broll"] = bool(beat_paths)
    item.meta["sources_used"] = credits
    item.save()


def next_publish_times(n: int, *, times: list[str] | None = None, tz: str | None = None) -> list[str]:
    """Compute the next `n` future publish slots as RFC-3339 UTC strings.

    Each configured HH:MM in `settings.publish_times` (interpreted in
    `settings.schedule_tz`, default UTC) becomes today's occurrence, rolled to
    tomorrow if already past. If more slots are needed than there are configured
    times, they spill onto following days. Used to stagger the daily batch's
    posts via native YouTube `publishAt`."""
    from datetime import datetime, timedelta, timezone

    times = times or settings.publish_times
    tzname = (tz or settings.schedule_tz or "UTC").strip()
    if tzname.upper() == "UTC":
        zone = timezone.utc
    else:
        try:
            from zoneinfo import ZoneInfo
            zone = ZoneInfo(tzname)
        except Exception:  # unknown tz / missing tzdata -> degrade to UTC
            print(f"  [schedule] unknown timezone {tzname!r}; using UTC")
            zone = timezone.utc

    now = datetime.now(zone)
    base: list[datetime] = []
    for t in times:
        try:
            hh, mm = (int(x) for x in t.split(":")[:2])
        except (ValueError, TypeError):
            continue
        cand = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if cand <= now:
            cand += timedelta(days=1)
        base.append(cand)
    if not base:  # misconfigured PUBLISH_TIMES — fall back to evenly-spaced slots
        base = [now + timedelta(hours=2 * (i + 1)) for i in range(max(n, 1))]
    base.sort()

    slots: list[datetime] = []
    day = 0
    while len(slots) < n:
        for s in base:
            slots.append(s + timedelta(days=day))
            if len(slots) >= n:
                break
        day += 1
    slots.sort()
    return [
        s.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        for s in slots[:n]
    ]


def schedule_item(item: review.Item, publish_at: str, *, targets: tuple[str, ...] = ("youtube",)) -> dict:
    """Approve and publish one rendered item with a native `publishAt` so YouTube
    auto-publishes it later. Returns the per-platform publish results."""
    from .publish import publish_item

    if not item.clip_path:
        raise RuntimeError(f"Item {item.id} has no clip to schedule.")
    review.approve(item.id)
    fresh = review.get(item.id)
    print(f"  -> scheduling {item.id} for {publish_at}")
    return publish_item(fresh, targets=targets, publish_at=publish_at)


def generate(count: int = 3, *, niche: str | None = None, seconds: int | None = None) -> list[review.Item]:
    """Discover trending topics and generate a review item for each."""
    print(f"Discovering {count} trending topics...")
    # Over-fetch a small buffer so topic-dedup doesn't starve the batch below target.
    topics = discover(count=count + 2, niche=niche)
    if not topics:
        print("No topics found.")
        return []
    # Drop stories we've covered recently (and intra-batch near-duplicates).
    topics = topic_history.filter_new(topics)[:count]
    if not topics:
        print("All discovered topics were recent duplicates; nothing to generate.")
        return []
    topic_history.remember(topics)
    items: list[review.Item] = []
    for i, topic in enumerate(topics, 1):
        print(f"[{i}/{len(topics)}] {topic.title}")
        try:
            items.append(generate_from_topic(topic, seconds=seconds))
        except Exception as e:  # one bad topic shouldn't kill the batch
            print(f"  !! failed: {e}")
    return items


def _is_speculative(meta: dict) -> bool:
    """True if the topic is inherently unverifiable subject matter (UFO/UAP/alien/
    paranormal). For these, 'needs_review' is expected and shouldn't block an
    auto-post — but 'rejected' (actively debunked) still does."""
    topic = meta.get("topic", {})
    haystack = " ".join([
        meta.get("topic_title", ""),
        topic.get("summary", ""),
        topic.get("why_trending", ""),
        " ".join(topic.get("keywords", [])),
    ]).lower()
    return any(
        re.search(rf"\b{re.escape(kw)}\b", haystack)
        for kw in settings.speculative_keywords
    )


def _publishable_verdict(meta: dict) -> bool:
    """Decide whether a clip clears the auto-publish gate. Normal topics need a
    clean 'ok'. Speculative ones (UFO/UAP/...) may publish on 'needs_review' too,
    since the claim is unconfirmable — but never on 'rejected' (debunked)."""
    verdict = meta.get("factcheck", {}).get("verdict")
    if verdict == factcheck.OK:
        return True
    return verdict == factcheck.NEEDS_REVIEW and _is_speculative(meta)


def auto_run(count: int = 3, *, targets: tuple[str, ...] = ("youtube", "facebook"),
             niche: str | None = None) -> list[dict]:
    """Fully automated: generate `count` clips and publish the ones that clear the
    fact-check gate, with no human review. Normal topics must pass cleanly ('ok');
    speculative UFO/UAP-style topics may also post on 'needs_review' (see
    SPECULATIVE_KEYWORDS), but anything 'rejected' (debunked) is always held.
    Returns publish results."""
    from .publish import publish_item

    items = generate(count=count, niche=niche)
    published: list[dict] = []
    for it in items:
        verdict = it.meta.get("factcheck", {}).get("verdict")
        if not it.clip_path:
            print(f"  [auto] hold {it.id}: no clip (verdict={verdict}), not publishing")
            continue
        if not _publishable_verdict(it.meta):
            print(f"  [auto] hold {it.id}: verdict={verdict}, not publishing")
            continue
        if verdict != factcheck.OK:
            print(f"  [auto] {it.id}: speculative topic — publishing despite verdict={verdict}")
        review.approve(it.id)
        fresh = review.get(it.id)
        print(f"  [auto] publishing {it.id} -> {targets}")
        try:
            results = publish_item(fresh, targets=targets)
            published.append({"id": it.id, "results": results})
        except Exception as e:
            print(f"  [auto] publish failed for {it.id}: {e}")
    print(f"[auto] done: published {len(published)}/{len(items)}")
    return published


def auto_run_custom_topic(topic: Topic, *, targets: tuple[str, ...] = ("youtube", "facebook")) -> list[dict]:
    """Fully automated for a user-provided topic (no trend discovery). Publishes
    unless the fact-checker actively debunks the content ('rejected'). A
    'needs_review' result still publishes — the user provided the content, so
    unverifiable claims are expected and trusted by default."""
    from .publish import publish_item

    it = generate_from_topic(topic)
    verdict = it.meta.get("factcheck", {}).get("verdict")
    if not it.clip_path:
        print(f"  [auto] hold {it.id}: no clip rendered")
        return []
    if verdict == factcheck.REJECTED:
        print(f"  [auto] hold {it.id}: fact-checker rejected the content — not publishing")
        return []
    if verdict != factcheck.OK:
        print(f"  [auto] {it.id}: user-provided topic — publishing despite verdict={verdict}")
    review.approve(it.id)
    fresh = review.get(it.id)
    print(f"  [auto] publishing {it.id} -> {targets}")
    try:
        results = publish_item(fresh, targets=targets)
        print(f"[auto] done: published 1/1")
        return [{"id": it.id, "results": results}]
    except Exception as e:
        print(f"  [auto] publish failed for {it.id}: {e}")
        return []


# ── promo mode (your own content: songs, products, links) ─────────────────────

def make_promo(
    *,
    title: str,
    audio: str | None = None,
    say: str | None = None,
    image: str | None = None,
    video: str | None = None,
    keywords: list[str] | None = None,
    link: str | None = None,
    cta: str | None = None,
    description: str | None = None,
    hashtags: list[str] | None = None,
    seconds: int | None = None,
    build_video: bool = True,
) -> review.Item:
    """Build a promo clip from YOUR content (no trends, no fact-check).

    Audio is either your own file (`audio`, e.g. a song) or AI voiceover of
    `say`. Visuals are your `video`/`image`, else stock from `keywords`, else a
    gradient. `link`/`cta` become a clickable call-to-action in the description.
    """
    if not audio and not say:
        raise RuntimeError("promo needs either --audio (your file) or --say (AI voiceover text)")

    meta = {
        "promo": True,
        "topic_title": title,
        "on_screen_title": title,
        "post_description": description or title,
        "link": link,
        "cta": cta,
        "hashtags": [h.lstrip("#").strip() for h in (hashtags or []) if h.strip()],
        "clip_seconds": seconds or settings.clip_seconds,
        # No fact-check: promo content is the user's own, not a factual claim.
        "factcheck": {"verdict": factcheck.OK, "summary": "promo (not fact-checked)"},
    }
    item = review.create(meta)
    if build_video:
        _render_promo(item, audio=audio, say=say, image=image, video=video,
                      keywords=keywords or [], seconds=seconds)
    print(f"  -> promo queued: {item.id}")
    return item


def _render_promo(item: review.Item, *, audio, say, image, video, keywords, seconds) -> None:
    work = item.dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    title = item.meta["on_screen_title"]

    # 1) Audio track: your file (trimmed to Shorts length) or AI voiceover.
    if audio:
        src = Path(audio).expanduser()
        if not src.exists():
            raise RuntimeError(f"audio file not found: {src}")
        full = _probe_duration(src) or float(seconds or settings.clip_seconds)
        cap = float(seconds) if seconds else min(full, _SHORTS_MAX)
        audio_path = trim_audio(src, item.dir / "audio.m4a", cap)
        duration, words = cap, []
        print(f"  -> promo audio: {src.name} ({duration:.1f}s)")
    else:
        voice = pick_voice()
        item.meta["voice"] = voice
        vo = synthesize(say, item.dir / "voice.mp3", voice=voice)
        audio_path, duration, words = vo.audio_path, vo.duration, vo.words
        print(f"  -> promo voiceover ({voice}, {duration:.1f}s)")

    # 2) Visuals: your video/image, else stock from keywords, else gradient.
    media: list[Path] = []
    for given in (video, image):
        if given:
            p = Path(given).expanduser()
            if not p.exists():
                raise RuntimeError(f"media file not found: {p}")
            media = [p]
            break
    if not media and keywords:
        media = stock.fetch_broll(keywords, work / "pexels", 6)

    # 3) Title overlay: persistent for a song (no captions), normal hook for voiceover.
    if words:
        ass = build_ass(words, item.dir / "captions.ass", hook=title)
    else:
        ass = build_ass([], item.dir / "captions.ass", title=title, title_seconds=duration)

    clip = assemble(
        audio_path=audio_path, ass_path=ass, broll_paths=media,
        out_path=item.dir / "clip.mp4", duration=duration, work_dir=work,
    )
    item.meta["clip"] = clip.name
    item.meta["duration"] = round(duration, 2)
    item.save()


def publish_promo(item: review.Item, *, targets: tuple[str, ...] = ("youtube",)) -> dict:
    """Approve and publish a promo item immediately (no review gate, no fact-check)."""
    from .publish import publish_item

    if not item.clip_path:
        print(f"  [promo] {item.id}: no clip rendered — not publishing")
        return {}
    review.approve(item.id)
    fresh = review.get(item.id)
    print(f"  [promo] publishing {item.id} -> {targets}")
    return publish_item(fresh, targets=targets)
