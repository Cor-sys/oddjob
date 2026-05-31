"""Fetch openly-licensed imagery of *specific subjects* from Wikimedia Commons.

Stock libraries rarely have footage of a named thing (a specific spacecraft,
person, place, machine). Commons does — much of it public domain or CC. We fetch
ONLY commercially-reusable licenses (PD / CC0 / CC BY / CC BY-SA), reject
NonCommercial/NoDerivatives, and default-reject anything we can't positively
classify, so a monetized channel stays clean. Attribution strings are collected
for CC-BY(-SA) compliance and surfaced in the post description.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import requests

from ..config import DATA_DIR

_API = "https://commons.wikimedia.org/w/api.php"
_USED_FILE = DATA_DIR / "used_commons.json"
_IMG_EXT = (".jpg", ".jpeg", ".png")
_UA = {"User-Agent": "OddjobBot/1.0 (automated mini-doc footage; github.com/Cor-sys/oddjob)"}

# License tokens we accept (commercial use OK), matched case-insensitively as
# substrings of Commons' LicenseShortName / License / UsageTerms.
_ALLOWED = ("cc0", "public domain", "cc by", "cc-by")
# Hard blockers — reject even if an allowed token also appears.
_BLOCKED = ("nc", "non-commercial", "noncommercial", "nd", "noderiv",
            "no derivative", "fair use", "all rights reserved")


@dataclass
class Asset:
    path: Path
    credit: str


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


def _tokens(blob: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", blob.lower()))


def _license_ok(extmet: dict) -> bool:
    """True only if the file is positively, commercially reusable."""
    def val(key: str) -> str:
        return str((extmet.get(key) or {}).get("value", ""))
    blob = " ".join([val("LicenseShortName"), val("License"), val("UsageTerms")]).lower()
    if not blob.strip():
        return False                       # unknown license -> reject
    toks = _tokens(blob)
    # "nc"/"nd" appear as standalone license tokens (e.g. "cc by-nc-sa" -> tokens
    # include 'nc'); match the blockers as whole tokens or substrings of phrases.
    if "nc" in toks or "nd" in toks:
        return False
    if any(b in blob for b in ("non-commercial", "noncommercial", "no derivative",
                               "noderiv", "fair use", "all rights reserved")):
        return False
    return any(a in blob for a in _ALLOWED)


def _credit(title: str, extmet: dict) -> str:
    raw_author = str((extmet.get("Artist") or {}).get("value", ""))
    author = re.sub(r"<[^>]+>", "", raw_author).strip() or "Wikimedia Commons"
    lic = str((extmet.get("LicenseShortName") or {}).get("value", "")).strip()
    name = title.replace("File:", "").strip()
    credit = f"{name} — {author}"
    return f"{credit} ({lic})" if lic else credit


def _search(query: str, limit: int = 8) -> list[dict]:
    params = {
        "action": "query", "format": "json",
        "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}",
        "gsrnamespace": "6",               # File: namespace
        "gsrlimit": str(limit),
        "prop": "imageinfo",
        "iiprop": "url|mime|extmetadata",
        "iiurlwidth": "1080",
    }
    try:
        r = requests.get(_API, params=params, headers=_UA, timeout=30)
        r.raise_for_status()
        pages = (r.json().get("query") or {}).get("pages") or {}
        return list(pages.values())
    except requests.RequestException as e:
        print(f"  [commons] '{query}' search failed: {e}")
        return []


def _download(url: str, out: Path) -> None:
    with requests.get(url, headers=_UA, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)


def fetch_media(keywords: list[str], dest_dir: Path, max_items: int = 6) -> list[Asset]:
    """Return up to max_items licensed Commons images for the given queries.
    One image per keyword, deduped across videos via used_commons.json."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    used = _load_used()
    assets: list[Asset] = []

    for kw in keywords:
        if len(assets) >= max_items:
            break
        for page in _search(kw):
            title = page.get("title", "")
            if not title or title in used:
                continue
            info = (page.get("imageinfo") or [{}])[0]
            extmet = info.get("extmetadata") or {}
            if "image" not in str(info.get("mime", "")):
                continue
            if not _license_ok(extmet):
                continue
            url = info.get("thumburl") or info.get("url")
            if not url or not url.lower().split("?")[0].endswith(_IMG_EXT):
                continue
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", title)[-60:]
            out = dest_dir / f"commons_{safe}.jpg"
            try:
                _download(url, out)
            except requests.RequestException as e:
                print(f"  [commons] download failed for {title}: {e}")
                continue
            assets.append(Asset(path=out, credit=_credit(title, extmet)))
            used.add(title)
            break  # one per keyword for topical variety

    _save_used(used)
    return assets
