"""Free AI voiceover via edge-tts, with per-word timings for captions.

Microsoft's service emits either WordBoundary or (for some voices) only
SentenceBoundary events. We normalize whatever we get into per-word timings,
falling back to an even spread across the true audio duration.
"""
from __future__ import annotations

import asyncio
import os
import random
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import edge_tts

from ..config import DATA_DIR, settings

FFPROBE = os.getenv("FFPROBE_BIN", "ffprobe")
_LAST_VOICE_FILE = DATA_DIR / "last_voice.txt"


@dataclass
class Word:
    text: str
    start: float  # seconds
    end: float    # seconds


@dataclass
class Boundary:
    text: str
    start: float
    end: float


@dataclass
class Voiceover:
    audio_path: Path
    words: list[Word]
    duration: float


async def _synthesize(text: str, voice: str, out_path: Path) -> list[Boundary]:
    communicate = edge_tts.Communicate(text, voice)
    bounds: list[Boundary] = []
    with open(out_path, "wb") as f:
        async for chunk in communicate.stream():
            ctype = chunk.get("type")
            if ctype == "audio":
                f.write(chunk["data"])
            elif ctype in ("WordBoundary", "SentenceBoundary"):
                start = chunk["offset"] / 1e7          # 100ns ticks -> seconds
                end = start + chunk["duration"] / 1e7
                bounds.append(Boundary(chunk["text"], start, end))
    return bounds


def _probe_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True,
        )
        return float(out.stdout.strip())
    except (ValueError, OSError):
        return 0.0


def _split_words(text: str) -> list[str]:
    return [w for w in re.findall(r"\S+", text) if w]


def _spread(words: list[str], start: float, end: float) -> list[Word]:
    """Distribute words across [start, end] proportional to their length."""
    if not words:
        return []
    weights = [max(1, len(w)) for w in words]
    total = sum(weights)
    span = max(0.01, end - start)
    out: list[Word] = []
    t = start
    for w, wt in zip(words, weights):
        dt = span * wt / total
        out.append(Word(w, t, t + dt))
        t += dt
    return out


def _to_words(bounds: list[Boundary], text: str, duration: float) -> list[Word]:
    # Case 1: already word-level (boundaries are single tokens)
    if bounds and all(len(_split_words(b.text)) <= 1 for b in bounds):
        return [Word(b.text, b.start, b.end) for b in bounds if b.text.strip()]
    # Case 2: sentence-level — split each sentence across its window
    if bounds:
        words: list[Word] = []
        for b in bounds:
            words.extend(_spread(_split_words(b.text), b.start, b.end))
        if words:
            return words
    # Case 3: nothing usable — spread the whole narration over the audio
    return _spread(_split_words(text), 0.0, duration or 1.0)


def synthesize(text: str, out_path: Path, voice: str | None = None) -> Voiceover:
    """Render `text` to an mp3 at `out_path`, returning audio + word timings."""
    voice = voice or settings.tts_voice
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bounds = asyncio.run(_synthesize(text, voice, out_path))

    duration = _probe_duration(out_path)
    if not duration:
        duration = max((b.end for b in bounds), default=1.0)

    words = _to_words(bounds, text, duration)
    return Voiceover(audio_path=out_path, words=words, duration=duration)


def pick_voice() -> str:
    """Choose a voice from the configured pool, avoiding the last one used so
    consecutive videos don't sound identical."""
    pool = settings.tts_voices or [settings.tts_voice]
    if len(pool) == 1:
        return pool[0]

    last = _LAST_VOICE_FILE.read_text(encoding="utf-8").strip() if _LAST_VOICE_FILE.exists() else ""
    choices = [v for v in pool if v != last] or pool
    choice = random.choice(choices)
    try:
        _LAST_VOICE_FILE.write_text(choice, encoding="utf-8")
    except OSError:
        pass
    return choice


def list_voices(language: str = "en") -> list[str]:
    async def _list() -> list[str]:
        voices = await edge_tts.list_voices()
        return [
            v["ShortName"]
            for v in voices
            if not language or v["Locale"].startswith(language)
        ]

    return sorted(asyncio.run(_list()))
