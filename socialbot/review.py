"""Human-review queue: every generated clip lands here before it can publish.

Each item is a folder under data/pending/<id>/ holding the clip plus a meta.json.
Approving keeps it in place (status flips); publishing moves it to data/published/.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import PENDING_DIR, PUBLISHED_DIR, RESERVE_DIR

# review statuses
PENDING = "pending"        # awaiting your decision
APPROVED = "approved"      # you approved it; eligible to publish
REJECTED = "rejected"      # you (or fact-check) rejected it
PUBLISHED = "published"    # posted to at least one platform
RESERVE = "reserve"        # tournament runner-up banked as a re-renderable recipe

META = "meta.json"


def _slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:n] or "clip"


def new_id(title: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}_{_slug(title)}"


@dataclass
class Item:
    dir: Path
    meta: dict[str, Any]

    @property
    def id(self) -> str:
        return self.dir.name

    @property
    def status(self) -> str:
        return self.meta.get("status", PENDING)

    @property
    def clip_path(self) -> Path | None:
        rel = self.meta.get("clip")
        p = self.dir / rel if rel else self.dir / "clip.mp4"
        return p if p.exists() else None

    def save(self) -> None:
        (self.dir / META).write_text(
            json.dumps(self.meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def set_status(self, status: str) -> None:
        self.meta["status"] = status
        self.meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.save()


def create(meta: dict[str, Any], *, base: Path = PENDING_DIR) -> Item:
    title = meta.get("on_screen_title") or meta.get("topic_title") or "clip"
    item_dir = base / new_id(title)
    item_dir.mkdir(parents=True, exist_ok=True)
    meta.setdefault("status", PENDING)
    meta.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    item = Item(dir=item_dir, meta=meta)
    item.save()
    return item


def _load(item_dir: Path) -> Item | None:
    meta_file = item_dir / META
    if not meta_file.exists():
        return None
    return Item(dir=item_dir, meta=json.loads(meta_file.read_text(encoding="utf-8")))


def get(item_id: str) -> Item | None:
    for base in (PENDING_DIR, PUBLISHED_DIR, RESERVE_DIR):
        item = _load(base / item_id)
        if item:
            return item
    return None


def list_items(status: str | None = None, *, base: Path = PENDING_DIR) -> list[Item]:
    items = [it for d in sorted(base.iterdir()) if d.is_dir() and (it := _load(d))]
    if status:
        items = [it for it in items if it.status == status]
    return items


def approve(item_id: str) -> Item:
    item = get(item_id)
    if not item:
        raise KeyError(item_id)
    item.set_status(APPROVED)
    return item


def reject(item_id: str, reason: str = "") -> Item:
    item = get(item_id)
    if not item:
        raise KeyError(item_id)
    if reason:
        item.meta["reject_reason"] = reason
    item.set_status(REJECTED)
    return item


def mark_published(item: Item, results: dict[str, Any]) -> Item:
    """Record publish results and move the item folder to data/published/."""
    item.meta["publish_results"] = results
    item.set_status(PUBLISHED)
    dest = PUBLISHED_DIR / item.id
    if item.dir.resolve() != dest.resolve():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(item.dir), str(dest))
        item.dir = dest
    return item
