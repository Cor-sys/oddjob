"""Reject degenerate footage frames before they end up in a video.

A near-black, blown-out, or solid-colour clip looks broken on screen. ffmpeg
`signalstats` on a sampled frame is the only thing that reliably flags these —
measured on real assets, empty clips read YAVG~16 while legit (even dark) space
imagery reads ~48+. Text-slides and off-topic-but-valid footage look like normal
frames to any pixel metric, so those are handled by routing/metadata, NOT here.

Best-effort by design: any error (no ffmpeg, unreadable file, unparseable output)
returns True, so the gate can never block a render — it only skips footage it is
confident is degenerate.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")

# Luma thresholds (0-255), calibrated on the real on-disk assets:
#   near-black junk clips: YAVG~16, YMAX~147       -> reject
#   good dark planet:      YAVG~48, YMAX 255        -> keep
#   good launch photo:     YAVG~130                 -> keep
# Reject near-black only when ALSO nothing bright is present, so a legit dark
# starfield (low YAVG, bright stars -> high YMAX) is kept.
_MIN_YAVG = 24.0       # darker than this ...
_DIM_YMAX = 170.0      # ... AND no bright element -> near-black / empty
_MAX_YAVG = 244.0      # brighter than this -> blown white
_MIN_RANGE = 28.0      # YMAX-YMIN below this -> near-uniform solid colour


def _signalstats(path: Path) -> dict[str, float] | None:
    """{YAVG, YMIN, YMAX} for one sampled frame (mid-clip for video), or None on
    any error."""
    seek = [] if path.suffix.lower() in _IMAGE_EXT else ["-ss", "0.5"]
    try:
        proc = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostats", *seek, "-i", str(path),
             "-vf", "signalstats,metadata=print", "-frames:v", "1", "-f", "null", "-"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    vals: dict[str, float] = {}
    for key in ("YAVG", "YMIN", "YMAX"):
        m = re.search(rf"lavfi\.signalstats\.{key}=([0-9.]+)", proc.stderr)
        if m:
            try:
                vals[key] = float(m.group(1))
            except ValueError:
                pass
    return vals or None


def usable(path: Path) -> bool:
    """True if the asset's sampled frame has real content — not near-black/empty,
    blown white, or a near-uniform solid colour. Conservative: returns True on any
    uncertainty so a measurable-but-fine clip is never dropped."""
    try:
        if not path.exists() or path.stat().st_size == 0:
            return False
    except OSError:
        return True

    stats = _signalstats(path)
    if not stats or "YAVG" not in stats:
        return True  # can't measure -> don't block

    yavg = stats["YAVG"]
    ymin = stats.get("YMIN")
    ymax = stats.get("YMAX")

    # near-black / empty: dark on average AND nothing bright in the frame
    if yavg < _MIN_YAVG and (ymax is None or ymax < _DIM_YMAX):
        return False
    # blown white
    if yavg > _MAX_YAVG:
        return False
    # near-uniform solid colour (almost no tonal range)
    if ymin is not None and ymax is not None and (ymax - ymin) < _MIN_RANGE:
        return False
    return True
