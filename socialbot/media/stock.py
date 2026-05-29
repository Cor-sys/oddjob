"""Fetch free vertical b-roll clips from Pexels.

Pexels returns results "most popular first", which is exactly the overused
footage every other channel grabs. To keep clips feeling unique we:
  - pull a wide pool (large per_page) and dig into a random deeper page,
  - skip the top-ranked (most popular) results,
  - shuffle and pick from what's left,
  - remember clip IDs we've already used so we never repeat across videos.
Falls back to [] (gradient background) when no API key is set.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import requests

from ..config import DATA_DIR, settings

_SEARCH = "https://api.pexels.com/videos/search"
_USED_FILE = DATA_DIR / "used_broll.json"

# how many of the most-popular results to skip per keyword
_SKIP_TOP = 4
_PER_PAGE = 40
# Stay within the first few pages: deeper pages drift off-topic. Uniqueness comes
# from shuffling + skipping the top + cross-video dedupe, not from going deep.
_MAX_PAGE = 3


def _load_used() -> set[int]:
    if _USED_FILE.exists():
        try:
            return set(json.loads(_USED_FILE.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            return set()
    return set()


def _save_used(used: set[int]) -> None:
    try:
        _USED_FILE.write_text(json.dumps(sorted(used)), encoding="utf-8")
    except OSError:
        pass


def _portrait_file(video: dict) -> str | None:
    """Pick a portrait file no taller than 1920 (largest such), else any portrait."""
    files = [f for f in video.get("video_files", []) if f.get("link")]
    portrait = [f for f in files if (f.get("height") or 0) >= (f.get("width") or 0)]
    pool = portrait or files
    if not pool:
        return None
    pool.sort(key=lambda f: (f.get("height") or 0))
    capped = [f for f in pool if (f.get("height") or 0) <= 1920] or pool
    return capped[-1]["link"]


def _search_page(headers: dict, query: str, page: int) -> dict:
    r = requests.get(
        _SEARCH,
        headers=headers,
        params={"query": query, "orientation": "portrait",
                "per_page": _PER_PAGE, "page": page, "size": "medium"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _pick_for_keyword(headers: dict, keyword: str, used: set[int]) -> dict | None:
    """Return a video dict for `keyword` that we haven't used, biased away from the
    most popular results."""
    # First page tells us how deep we can go.
    try:
        first = _search_page(headers, keyword, 1)
    except requests.RequestException as e:
        print(f"  [stock] '{keyword}' search failed: {e}")
        return None

    total = first.get("total_results", 0)
    # Only diversify across pages when there are plenty of relevant matches;
    # for thin result sets, stay on page 1 to keep footage on-topic.
    pages = max(1, min(_MAX_PAGE, (total // _PER_PAGE) + 1))
    page = random.randint(1, pages)
    data = first if page == 1 else _search_page(headers, keyword, page)

    videos = data.get("videos", [])
    # only skip the most-popular handful when we still have plenty left to pick from
    if page == 1 and len(videos) > _SKIP_TOP * 2:
        videos = videos[_SKIP_TOP:]
    random.shuffle(videos)

    for v in videos:
        if v.get("id") not in used and _portrait_file(v):
            return v
    return None


def fetch_broll(keywords: list[str], dest_dir: Path, max_clips: int = 6) -> list[Path]:
    """Download one distinct, non-overused clip per keyword (up to max_clips)."""
    if not settings.pexels_api_key or not keywords:
        return []

    dest_dir.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": settings.pexels_api_key}
    used = _load_used()
    paths: list[Path] = []

    for kw in keywords:
        if len(paths) >= max_clips:
            break
        video = _pick_for_keyword(headers, kw, used)
        if not video:
            continue
        vid = video["id"]
        link = _portrait_file(video)
        out = dest_dir / f"broll_{vid}.mp4"
        try:
            _download(link, out, headers)
            paths.append(out)
            used.add(vid)
        except requests.RequestException as e:
            print(f"  [stock] download failed for {vid}: {e}")

    _save_used(used)
    return paths


def _download(url: str, out: Path, headers: dict) -> None:
    with requests.get(url, headers=headers, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
