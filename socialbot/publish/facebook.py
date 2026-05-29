"""Publish a clip to a Facebook Page via the Graph API."""
from __future__ import annotations

from pathlib import Path

import requests

from ..config import settings

GRAPH = "https://graph.facebook.com/v21.0"


def upload(video_path: Path, title: str, description: str) -> dict:
    if not settings.facebook_page_id or not settings.facebook_page_token:
        raise RuntimeError("FACEBOOK_PAGE_ID / FACEBOOK_PAGE_TOKEN not set. See SETUP.md.")

    url = f"{GRAPH}/{settings.facebook_page_id}/videos"
    data = {
        "title": title[:255],
        "description": description,
        "access_token": settings.facebook_page_token,
    }
    with open(video_path, "rb") as f:
        resp = requests.post(url, data=data, files={"source": f}, timeout=600)

    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise RuntimeError(f"Facebook upload failed: {resp.text}") from e

    vid = resp.json().get("id", "")
    return {
        "platform": "facebook",
        "id": vid,
        "url": f"https://www.facebook.com/{vid}" if vid else "",
    }
