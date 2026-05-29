"""Orchestrates: trends -> script -> fact-check -> media -> review queue."""
from __future__ import annotations

from . import factcheck, review
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

    print(f"  -> queued for review: {item.id}")
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
    topics = discover(count=count, niche=niche)
    if not topics:
        print("No topics found.")
        return []
    items: list[review.Item] = []
    for i, topic in enumerate(topics, 1):
        print(f"[{i}/{len(topics)}] {topic.title}")
        try:
            items.append(generate_from_topic(topic, seconds=seconds))
        except Exception as e:  # one bad topic shouldn't kill the batch
            print(f"  !! failed: {e}")
    return items


def auto_run(count: int = 3, *, targets: tuple[str, ...] = ("youtube", "facebook"),
             niche: str | None = None) -> list[dict]:
    """Fully automated: generate `count` clips and publish the ones that PASS
    fact-check (verdict 'ok') with no human review. Clips that are needs_review
    or rejected are left in the queue (not posted). Returns publish results."""
    from .factcheck import OK
    from .publish import publish_item

    items = generate(count=count, niche=niche)
    published: list[dict] = []
    for it in items:
        verdict = it.meta.get("factcheck", {}).get("verdict")
        if verdict != OK or not it.clip_path:
            print(f"  [auto] hold {it.id}: verdict={verdict}, not publishing")
            continue
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
