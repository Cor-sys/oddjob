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
    # Credit openly-licensed footage (Wikimedia Commons CC-BY etc.) for compliance.
    credits = meta.get("sources_used") or []
    if credits:
        desc = f"{desc}\n\nFootage credits: " + "; ".join(credits[:8])
    if hashtags:
        desc = f"{desc}\n\n" + " ".join(f"#{h.lstrip('#')}" for h in hashtags)
    return title, desc.strip(), hashtags


def publish_item(item: review.Item, targets: tuple[str, ...] = TARGETS,
                 publish_at: str | None = None) -> dict:
    """Publish one approved item. Returns per-platform results.

    `publish_at` (RFC-3339) schedules the YouTube upload for later via native
    `publishAt` (uploaded private, auto-published at that time). Facebook has no
    equivalent here, so it's skipped when scheduling to keep the stagger intact."""
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
                results["youtube"] = youtube.upload(
                    clip, title, description, tags=hashtags, publish_at=publish_at
                )
                yt = results["youtube"]
                when = f" (scheduled {yt['publish_at']})" if yt.get("publish_at") else ""
                print(f"  youtube -> {yt.get('url')}{when}")
            except Exception as e:
                results["youtube"] = {"error": str(e)}
                print(f"  youtube FAILED: {e}")

        if "facebook" in targets and not publish_at:
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
