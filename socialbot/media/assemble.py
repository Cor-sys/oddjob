"""Compose the final 1080x1920 mp4 with ffmpeg.

Layers: background (stock b-roll, or a generated gradient) + burned-in captions
+ the voiceover audio track.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

W, H, FPS = 1080, 1920, 30
FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")

_NORMALIZE = (
    f"scale={W}:{H}:force_original_aspect_ratio=increase,"
    f"crop={W}:{H},fps={FPS},setsar=1,format=yuv420p"
)


def _run(args: list[str], cwd: Path | None = None) -> None:
    proc = subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{proc.stderr.strip()}")


def _make_gradient_bg(out: Path, duration: float, work: Path) -> None:
    src = (
        f"gradients=s={W}x{H}:c0=0x141e30:c1=0x2d1b4e:c2=0x0b3a53:"
        f"x0=0:y0=0:x1={W}:y1={H}:nb_colors=3:speed=0.008:d={duration:.2f}:r={FPS}"
    )
    _run(["-f", "lavfi", "-i", src, "-t", f"{duration:.2f}",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
          "-pix_fmt", "yuv420p", out.name], cwd=work)


# Fast cuts hold attention: top-performing Shorts average a visual change every
# 2-4 seconds. We aim for ~3s segments and cycle the available media to fill the
# clip, rather than stretching a couple of clips across the whole runtime.
_TARGET_SEG = 3.0


def _video_segment(clip: Path, seg_name: str, seg: float, work: Path, start: float = 0.0) -> None:
    # `start` seeks into the (looped) clip so a reused clip doesn't replay the
    # same opening frames back-to-back.
    pre = ["-stream_loop", "-1"]
    if start > 0.05:
        pre += ["-ss", f"{start:.2f}"]
    _run([*pre, "-i", str(clip.resolve()), "-t", f"{seg:.2f}",
          "-an", "-vf", _NORMALIZE,
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
          "-r", str(FPS), seg_name], cwd=work)


def _image_segment(img: Path, seg_name: str, seg: float, work: Path, zoom_out: bool = False) -> None:
    """Turn a still image into a slow Ken Burns (pan/zoom) video segment. Reused
    images alternate zoom-in/zoom-out so a repeat doesn't look like a static hold."""
    frames = max(2, int(round(seg * FPS)))
    # Frame-indexed zoom (`on` = output frame) so direction is deterministic.
    if zoom_out:
        z = f"max(1.35-0.0012*on,1.0)"
    else:
        z = f"min(1.0+0.0012*on,1.35)"
    # Pre-scale large so the zoom stays crisp.
    vf = (
        f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,"
        f"crop={W * 2}:{H * 2},"
        f"zoompan=z='{z}':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},"
        f"setsar=1,format=yuv420p"
    )
    _run(["-i", str(img.resolve()), "-vf", vf, "-frames:v", str(frames),
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
          "-r", str(FPS), seg_name], cwd=work)


def _make_media_bg(out: Path, media: list[Path], duration: float, work: Path) -> None:
    """Build the background from a mix of video clips and still images, cutting
    every ~3s. With 2+ sources we cycle through them so the frame keeps changing;
    a single source is left as one continuous segment (cutting to itself stutters)."""
    total = duration + 1.5
    if len(media) >= 2:
        # Enough segments to hit ~3s cuts, and at least one per source.
        n_seg = max(len(media), round(total / _TARGET_SEG))
    else:
        n_seg = 1
    seg = total / n_seg

    segments: list[str] = []
    uses: dict[int, int] = {}
    for i in range(n_seg):
        src = i % len(media)
        reuse = uses.get(src, 0)
        uses[src] = reuse + 1
        path = media[src]
        seg_name = f"seg_{i}.mp4"
        if path.suffix.lower() in _IMAGE_EXT:
            _image_segment(path, seg_name, seg, work, zoom_out=bool(reuse % 2))
        else:
            _video_segment(path, seg_name, seg, work, start=reuse * seg)
        segments.append(seg_name)

    list_file = work / "concat.txt"
    list_file.write_text("".join(f"file '{s}'\n" for s in segments), encoding="utf-8")
    _run(["-f", "concat", "-safe", "0", "-i", list_file.name, "-c", "copy", out.name],
         cwd=work)


def assemble(
    audio_path: Path,
    ass_path: Path,
    broll_paths: list[Path],
    out_path: Path,
    duration: float,
    work_dir: Path,
) -> Path:
    """Build the final clip. Runs ffmpeg with cwd=work_dir to avoid path-escaping
    issues with the ass/concat filters on Windows."""
    work_dir.mkdir(parents=True, exist_ok=True)

    # captions file must live in work_dir so `ass=captions.ass` resolves cleanly
    captions = work_dir / "captions.ass"
    if ass_path.resolve() != captions.resolve():
        captions.write_text(ass_path.read_text(encoding="utf-8"), encoding="utf-8")

    bg = work_dir / "bg.mp4"
    if broll_paths:
        _make_media_bg(bg, broll_paths, duration, work_dir)
    else:
        _make_gradient_bg(bg, duration + 1.5, work_dir)

    # final: burn captions, attach audio, end at the (shorter) audio track
    out_path.parent.mkdir(parents=True, exist_ok=True)
    final_tmp = work_dir / "final.mp4"
    _run([
        "-i", bg.name,
        "-i", str(audio_path.resolve()),
        "-vf", "ass=captions.ass",
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
        "-shortest", "-movflags", "+faststart",
        final_tmp.name,
    ], cwd=work_dir)

    out_path.write_bytes(final_tmp.read_bytes())
    return out_path
