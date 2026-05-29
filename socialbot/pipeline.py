"""Orchestrates: trends -> script -> fact-check -> media -> review queue."""
from __future__ import annotations

import re

from . import costs, factcheck, review, topic_history
from .config import settings
from .media.assemble import assemble
from .media.captions import build_ass
from .media.router import fetch_visuals
from .media.tts import pick_voice, synthesize
from .script import Script, write_script
from .trends import Topic, discover


def generate_from_topic(topic: Topic, *, seconds: int | None = None, build_video: bool = True) -> review.Item:
    """Run one topic through the full pipeline, returning a queued review Item."""
    seconds = seconds or settings.clip_seconds
    # Tag every Gemini call below with this topic so its spend rolls up per-video.
    with costs.track(topic=topic.title) as run:
        print(f"  -> scripting: {topic.title}")
        script = write_script(topic, seconds)

        print("  -> fact-checking...")
        fc = factcheck.vet(script)
        print(f"     verdict={fc.verdict} ({fc.summary})")

        meta = {
            "topic": topic.to_dict(),
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


def _render(item: review.Item, script: Script, topic: Topic) -> None:
    work = item.dir / "work"
    work.mkdir(parents=True, exist_ok=True)

    voice = pick_voice()
    print(f"  -> voiceover ({voice})...")
    vo = synthesize(script.narration, item.dir / "voice.mp3", voice=voice)
    item.meta["voice"] = voice

    print("  -> captions...")
    ass = build_ass(vo.words, item.dir / "captions.ass", title=script.on_screen_title)

    print("  -> visuals...")
    media = fetch_visuals(topic, script, work)
    if not media:
        print("     (no footage available — using gradient background)")

    print("  -> assembling...")
    clip = assemble(
        audio_path=vo.audio_path,
        ass_path=ass,
        broll_paths=media,
        out_path=item.dir / "clip.mp4",
        duration=vo.duration,
        work_dir=work,
    )

    item.meta["clip"] = clip.name
    item.meta["duration"] = round(vo.duration, 2)
    item.meta["used_broll"] = bool(media)
    item.save()


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
