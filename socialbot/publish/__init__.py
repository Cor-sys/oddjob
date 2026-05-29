"""Publish approved review items to the configured platforms."""
from __future__ import annotations

from .. import costs, review

TARGETS = ("youtube", "facebook")


def _title_and_description(item: review.Item) -> tuple[str, str, list[str]]:
    script = item.meta.get("script", {})
    title = item.meta.get("on_screen_title") or item.meta.get("topic_title") or "Short"
    hashtags = script.get("hashtags", [])
    desc = script.get("description", "")
    if hashtags:
        desc = f"{desc}\n\n" + " ".join(f"#{h}" for h in hashtags)
    return title, desc.strip(), hashtags


def publish_item(item: review.Item, targets: tuple[str, ...] = TARGETS) -> dict:
    """Publish one approved item. Returns per-platform results."""
    if item.status not in (review.APPROVED,):
        raise RuntimeError(
            f"Item {item.id} is '{item.status}', not approved. Approve it first."
        )
    clip = item.clip_path
    if not clip:
        raise RuntimeError(f"Item {item.id} has no clip to publish.")

    title, description, hashtags = _title_and_description(item)
    results: dict[str, dict] = {}

    # Tag any spend/quota recorded during publishing with this item + topic.
    with costs.track(item_id=item.id, topic=item.meta.get("topic_title"), stage="publish"):
        if "youtube" in targets:
            try:
                from . import youtube
                results["youtube"] = youtube.upload(clip, title, description, tags=hashtags)
                print(f"  youtube -> {results['youtube'].get('url')}")
            except Exception as e:
                results["youtube"] = {"error": str(e)}
                print(f"  youtube FAILED: {e}")

        if "facebook" in targets:
            try:
                from . import facebook
                results["facebook"] = facebook.upload(clip, title, description)
                print(f"  facebook -> {results['facebook'].get('url')}")
            except Exception as e:
                results["facebook"] = {"error": str(e)}
                print(f"  facebook FAILED: {e}")

    if any("error" not in r for r in results.values()):
        review.mark_published(item, results)
    return results
