"""Build a styled .ass subtitle file from word timings (burned in by ffmpeg)."""
from __future__ import annotations

from pathlib import Path

from .tts import Word

# 1080x1920 vertical canvas
PLAY_W, PLAY_H = 1080, 1920

_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_W}
PlayResY: {PLAY_H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,86,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,5,3,2,90,90,720,1
Style: Title,Arial Black,104,&H0000E5FF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,6,4,8,80,80,260,1
Style: Hook,Arial Black,92,&H00FFFFFF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,7,3,5,120,120,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(seconds: float) -> str:
    # ASS timestamp: H:MM:SS.cc (centiseconds)
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _clean(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def _group(words: list[Word], max_words: int, max_dur: float) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    bucket: list[Word] = []
    for w in words:
        if bucket and (
            len(bucket) >= max_words or (w.end - bucket[0].start) > max_dur
        ):
            cues.append((bucket[0].start, bucket[-1].end, " ".join(x.text for x in bucket)))
            bucket = []
        bucket.append(w)
    if bucket:
        cues.append((bucket[0].start, bucket[-1].end, " ".join(x.text for x in bucket)))
    return cues


def build_ass(
    words: list[Word],
    out_path: Path,
    *,
    title: str = "",
    hook: str = "",
    title_seconds: float = 3.0,
    hook_seconds: float = 2.5,
    max_words: int = 4,
    max_dur: float = 1.8,
) -> Path:
    lines = [_HEADER]

    # A hook is the scroll-stopper: big, centered, the first ~2.5s. When present
    # it serves as the opener (instead of the small top title card) and the
    # bottom captions are held back until it clears, so there's no double-text.
    caption_floor = 0.0
    if hook:
        lines.append(
            f"Dialogue: 0,{_ts(0)},{_ts(hook_seconds)},Hook,,0,0,0,,{_clean(hook).upper()}"
        )
        caption_floor = hook_seconds
    elif title:
        lines.append(
            f"Dialogue: 0,{_ts(0)},{_ts(title_seconds)},Title,,0,0,0,,{_clean(title).upper()}"
        )

    for start, end, text in _group(words, max_words, max_dur):
        if start < caption_floor:        # covered by the hook overlay — skip
            continue
        lines.append(
            f"Dialogue: 0,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{_clean(text).upper()}"
        )

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
