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
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from ..config import DATA_DIR
from . import quality

_API = "https://commons.wikimedia.org/w/api.php"
_USED_FILE = DATA_DIR / "used_commons.json"
_IMG_EXT = (".jpg", ".jpeg", ".png")
_UA = {"User-Agent": "OddjobBot/1.0 (automated mini-doc footage; github.com/Cor-sys/oddjob)"}

# upload.wikimedia.org throttles bursts hard (429 + "contact noc@wikimedia.org").
# Pace every request so we never trip the limiter in the first place; the retry
# backoff below is the fallback if we do. But a sustained 429 streak means the IP
# is *blocked*, not merely paced — backoff can't fix that, so a circuit breaker
# abandons Commons after _THROTTLE_BREAKER consecutive 429s and lets the caller
# fall back to NASA/stock fast instead of grinding for tens of minutes.
_MIN_INTERVAL = 1.0          # min seconds between successive Wikimedia requests
_THROTTLE_BREAKER = 3        # consecutive 429-failed images before we give up
_last_request = 0.0          # monotonic timestamp of the last request we made


def _throttle() -> None:
    """Block until at least _MIN_INTERVAL has passed since the last request."""
    global _last_request
    wait = _MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _retry_after(resp, attempt: int) -> float:
    """Seconds to wait before a retry. Prefer the server's Retry-After header;
    otherwise exponential backoff (3, 6, 12, ...), capped at 30s."""
    val = (getattr(resp, "headers", None) or {}).get("Retry-After")
    if val:
        try:
            return min(float(val), 8.0)    # Wikimedia sends integer seconds
        except (TypeError, ValueError):
            pass                           # HTTP-date form -> fall through
    return min(3.0 * (2 ** attempt), 8.0)

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
        _throttle()
        r = requests.get(_API, params=params, headers=_UA, timeout=30)
        r.raise_for_status()
        pages = (r.json().get("query") or {}).get("pages") or {}
        return list(pages.values())
    except requests.RequestException as e:
        print(f"  [commons] '{query}' search failed: {e}")
        return []


def _download(url: str, out: Path, *, tries: int = 2) -> None:
    """Download a Commons image, retrying on rate-limit/overload. upload.wikimedia
    .org throttles bursts (429); we honor Retry-After when present and otherwise
    back off exponentially so the fetch succeeds instead of silently giving up and
    falling back to NASA. Kept to a single short retry — a *sustained* 429 streak
    is an IP block that no backoff clears, so the caller's circuit breaker bails."""
    for attempt in range(tries):
        try:
            _throttle()
            with requests.get(url, headers=_UA, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(out, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 16):
                        f.write(chunk)
            return
        except requests.RequestException as e:
            resp = getattr(e, "response", None)
            status = getattr(resp, "status_code", None)
            if attempt < tries - 1 and status in (429, 503):
                time.sleep(_retry_after(resp, attempt))
                continue
            raise


def fetch_media(keywords: list[str], dest_dir: Path, max_items: int = 6) -> list[Asset]:
    """Return up to max_items licensed Commons images for the given queries.
    One image per keyword, deduped across videos via used_commons.json."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    used = _load_used()
    assets: list[Asset] = []
    throttled = 0          # consecutive 429-failed downloads -> trip the breaker

    for kw in keywords:
        if len(assets) >= max_items or throttled >= _THROTTLE_BREAKER:
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
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status == 429:
                    throttled += 1
                print(f"  [commons] download failed for {title}: {e}")
                if throttled >= _THROTTLE_BREAKER:
                    print(f"  [commons] {throttled} consecutive 429s - IP is "
                          "throttled; abandoning Commons for this run (falling "
                          "back to NASA/stock).")
                    break
                continue
            throttled = 0  # a success means we're not blocked; reset the breaker
            # Skip degenerate images (near-black/blown/solid); try the next result.
            if not quality.usable(out):
                print(f"  [commons] skipping low-quality image {title}")
                out.unlink(missing_ok=True)
                used.add(title)
                continue
            assets.append(Asset(path=out, credit=_credit(title, extmet)))
            used.add(title)
            break  # one per keyword for topical variety

    _save_used(used)
    return assets
