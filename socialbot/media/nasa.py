"""Fetch free, public-domain space media from NASA's Image and Video Library.

No API key or account required (images-api.nasa.gov is fully open). Everything
here is US-government public domain, so there's no copyright-strike risk.
Perfect for the space/astronomy side of the channel where stock footage fails.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import requests

from ..config import DATA_DIR

_SEARCH = "https://images-api.nasa.gov/search"
_ASSET = "https://images-api.nasa.gov/asset/{nasa_id}"
_USED_FILE = DATA_DIR / "used_nasa.json"

_IMG_EXT = (".jpg", ".jpeg", ".png")

# NASA's library mixes real photography with illustrations, artist's concepts,
# schematics and charts. For footage we want real imagery, so we skip items whose
# metadata signals a non-photographic graphic (that's where the Hubble "blueprint"
# came from).
_NONPHOTO = (
    "illustration", "artist concept", "artist's concept", "artists concept",
    "rendering", "render of", "diagram", "schematic", "infographic", "chart",
    "concept art", "cutaway", "blueprint", "graphic of", "logo", "poster",
)


def _is_photo(data: dict) -> bool:
    blob = " ".join([
        str(data.get("title", "")), str(data.get("description", "")),
        " ".join(data.get("keywords") or []),
    ]).lower()
    return not any(t in blob for t in _NONPHOTO)


def _load_used() -> set[str]:
    if _USED_FILE.exists():
        try:
            return set(json.loads(_USED_FILE.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            return set()
    return set()


def _save_used(used: set[str]) -> None:
    try:
        _USED_FILE.write_text(json.dumps(sorted(used)), encoding="utf-8")
    except OSError:
        pass


def _search(query: str, media_type: str) -> list[dict]:
    try:
        r = requests.get(
            _SEARCH, params={"q": query, "media_type": media_type}, timeout=30
        )
        r.raise_for_status()
        return r.json().get("collection", {}).get("items", [])
    except requests.RequestException as e:
        print(f"  [nasa] '{query}' search failed: {e}")
        return []


def _asset_hrefs(nasa_id: str) -> list[str]:
    try:
        r = requests.get(_ASSET.format(nasa_id=nasa_id), timeout=30)
        r.raise_for_status()
        items = r.json().get("collection", {}).get("items", [])
        return [i["href"] for i in items if i.get("href")]
    except requests.RequestException:
        return []


def _pick_href(hrefs: list[str], exts: tuple[str, ...], prefer: tuple[str, ...]) -> str | None:
    cands = [h for h in hrefs if h.lower().split("?")[0].endswith(exts)]
    for tag in prefer:
        for h in cands:
            if tag in h.lower():
                return h
    return cands[0] if cands else None


def _download(url: str, out: Path) -> None:
    # NASA asset URLs are sometimes http; force https to avoid redirects.
    url = url.replace("http://", "https://", 1)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)


def _fetch(keywords: list[str], dest_dir: Path, media_type: str, max_items: int) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    used = _load_used()
    paths: list[Path] = []

    if media_type == "video":
        exts, prefer, suffix = (".mp4",), ("~medium", "~small", "~mobile", "~large"), "mp4"
    else:
        exts, prefer, suffix = _IMG_EXT, ("~medium", "~small", "~large"), "jpg"

    for kw in keywords:
        if len(paths) >= max_items:
            break
        items = _search(kw, media_type)
        random.shuffle(items)  # vary which on-topic result we use
        for item in items:
            data = (item.get("data") or [{}])[0]
            nasa_id = data.get("nasa_id")
            if not nasa_id or nasa_id in used:
                continue
            # Photos only — skip diagrams / artist's concepts / schematics.
            if media_type == "image" and not _is_photo(data):
                continue
            href = _pick_href(_asset_hrefs(nasa_id), exts, prefer)
            if not href:
                continue
            out = dest_dir / f"nasa_{nasa_id}.{suffix}"
            try:
                _download(href, out)
            except requests.RequestException as e:
                print(f"  [nasa] download failed for {nasa_id}: {e}")
                continue
            paths.append(out)
            used.add(nasa_id)
            break  # one clip per keyword for topical variety

    _save_used(used)
    return paths


def fetch_images(keywords: list[str], dest_dir: Path, max_items: int = 6) -> list[Path]:
    return _fetch(keywords, dest_dir, "image", max_items)


def fetch_videos(keywords: list[str], dest_dir: Path, max_items: int = 6) -> list[Path]:
    return _fetch(keywords, dest_dir, "video", max_items)


def fetch_media(keywords: list[str], dest_dir: Path, max_items: int = 6) -> list[Path]:
    """Prefer NASA images (plentiful, stunning for space); top up with video."""
    paths = fetch_images(keywords, dest_dir, max_items)
    if len(paths) < max_items:
        paths += fetch_videos(keywords, dest_dir, max_items - len(paths))
    return paths
