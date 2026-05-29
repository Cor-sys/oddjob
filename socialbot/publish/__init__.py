"""Publish approved review items to the configured platforms."""
from __future__ import annotations

from .. import costs, review

TARGETS = ("youtube", "facebook")


def _title_and_description(item: review.Item) -> tuple[str, str, list[str]]:
    meta = item.meta
    script = meta.get("script", {})
    title = meta.get("on_screen_title") or meta.get("topic_title") or "Short"
    # Promo posts carry their own hashtags/description on meta; normal videos
    # use the AI-written script fields.
    hashtags = meta.get("hashtags") or script.get("hashtags", [])
    desc = meta.get("post_description") or script.get("description", "")
    # Optional call-to-action + link (songs, products, websites). The link is
    # written on its own line so it stays clickable in the post description.
    cta, link = meta.get("cta"), meta.get("link")
    if link:
        desc = f"{desc}\n\n{(cta + ': ') if cta else ''}{link}".strip()
    elif cta:
        desc = f"{desc}\n\n{cta}".strip()
    if hashtags:
        desc = f"{desc}\n\n" + " ".join(f"#{h.lstrip('#')}" for h in hashtags)
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
