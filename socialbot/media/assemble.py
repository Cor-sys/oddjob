"""Compose the final 1080x1920 mp4 with ffmpeg.

Layers: background (stock b-roll, or a generated gradient) + burned-in captions
+ the voiceover audio track.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..config import ROOT, settings

W, H, FPS = 1080, 1920, 30
FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
_AUDIO_EXT = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac")

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


def trim_audio(src: Path, dst: Path, seconds: float, *, fade: float = 1.0) -> Path:
    """Copy `src` audio to `dst`, trimmed to `seconds` with a short fade-out so a
    song/clip ends cleanly instead of cutting off mid-note. Used by promo mode."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    st = max(0.0, seconds - fade)
    _run(["-i", str(src.resolve()), "-t", f"{seconds:.2f}",
          "-af", f"afade=t=out:st={st:.2f}:d={fade:.2f}",
          "-c:a", "aac", "-b:a", "192k", str(dst.resolve())])
    return dst


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


# Beats shorter than this would stutter as their own cut, so they merge forward.
_MIN_SEG = 1.0


def _make_beat_bg(out: Path, beats: list[tuple[float, float, Path]], duration: float, work: Path) -> None:
    """Background cut to the narration: one segment per shot-list beat, timed to
    that beat. Tiles the timeline (segment i runs from beat i's start to the next
    beat's start); sub-1s beats merge into the previous segment so cuts don't
    stutter; the last segment is padded to cover the audio tail."""
    tiled: list[list] = []
    for i, (start, _end, path) in enumerate(beats):
        nxt = beats[i + 1][0] if i + 1 < len(beats) else (duration + 1.0)
        tiled.append([max(0.0, nxt - start), path])

    merged: list[list] = []
    for seg, path in tiled:
        if path is None:
            if merged:
                merged[-1][0] += seg
            continue
        if merged and seg < _MIN_SEG:
            merged[-1][0] += seg
        else:
            merged.append([seg, path])

    if not merged:
        _make_gradient_bg(out, duration + 1.5, work)
        return

    pad = (duration + 1.0) - sum(s for s, _ in merged)
    if pad > 0:
        merged[-1][0] += pad

    segments: list[str] = []
    for i, (seg, path) in enumerate(merged):
        seg = max(_MIN_SEG, seg)
        seg_name = f"seg_{i}.mp4"
        if path.suffix.lower() in _IMAGE_EXT:
            _image_segment(path, seg_name, seg, work, zoom_out=bool(i % 2))
        else:
            _video_segment(path, seg_name, seg, work)
        segments.append(seg_name)

    list_file = work / "concat.txt"
    list_file.write_text("".join(f"file '{s}'\n" for s in segments), encoding="utf-8")
    _run(["-f", "concat", "-safe", "0", "-i", list_file.name, "-c", "copy", out.name],
         cwd=work)


def _resolve_logo() -> Path | None:
    if not settings.branding_enabled:
        return None
    p = ROOT / settings.logo_path
    return p if p.exists() else None


def _resolve_music() -> Path | None:
    if not settings.music_enabled:
        return None
    d = ROOT / settings.music_dir
    if not d.is_dir():
        return None
    tracks = sorted(p for p in d.iterdir() if p.suffix.lower() in _AUDIO_EXT)
    return tracks[0] if tracks else None


def assemble(
    audio_path: Path,
    ass_path: Path,
    broll_paths: list[Path],
    out_path: Path,
    duration: float,
    work_dir: Path,
    *,
    beats: list[tuple[float, float, Path]] | None = None,
) -> Path:
    """Build the final clip. Runs ffmpeg with cwd=work_dir to avoid path-escaping
    issues with the ass/concat filters on Windows.

    When `beats` is given (a (start, end, path) per narration beat) the background
    is cut to the narration; otherwise it falls back to even ~3s cuts over
    `broll_paths`, then a gradient. Branding (corner logo bug + logo outro card)
    and an optional music bed are applied in the final pass per config."""
    work_dir.mkdir(parents=True, exist_ok=True)

    # captions file must live in work_dir so `ass=captions.ass` resolves cleanly
    captions = work_dir / "captions.ass"
    if ass_path.resolve() != captions.resolve():
        captions.write_text(ass_path.read_text(encoding="utf-8"), encoding="utf-8")

    bg = work_dir / "bg.mp4"
    if beats:
        _make_beat_bg(bg, beats, duration, work_dir)
    elif broll_paths:
        _make_media_bg(bg, broll_paths, duration, work_dir)
    else:
        _make_gradient_bg(bg, duration + 1.5, work_dir)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    final_tmp = work_dir / "final.mp4"
    cmd = _compose_cmd(bg, audio_path, duration, final_tmp.name)
    _run(cmd, cwd=work_dir)

    out_path.write_bytes(final_tmp.read_bytes())
    return out_path


def _compose_cmd(bg: Path, audio_path: Path, duration: float, out_name: str) -> list[str]:
    """Build the final ffmpeg arg list: background + captions, plus a corner logo
    bug and a brief logo outro card (full-screen, last ENDCARD_SECONDS), plus an
    optional music bed mixed under the voiceover. Inputs: 0=bg, 1=voiceover,
    then optional 2=logo, 3=music."""
    logo = _resolve_logo()
    music = _resolve_music()

    inputs = ["-i", bg.name, "-i", str(audio_path.resolve())]
    idx = 2

    if logo:
        logo_idx = idx
        idx += 1
        inputs += ["-i", str(logo.resolve())]
        end_start = max(0.0, duration - settings.endcard_seconds)
        bug_w = settings.logo_scale_w
        card_w = int(W * 0.42)
        # The logo is a circular badge on an opaque black square, so we punch a
        # circular alpha mask (keep the inscribed disc, drop the black corners)
        # instead of dimming the whole box. Applied to both the corner bug and
        # the full-screen outro card.
        mask = (
            "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            "a='if(lte((X-W/2)*(X-W/2)+(Y-H/2)*(Y-H/2),(W/2)*(W/2)),255,0)'"
        )
        vfilter = (
            f"[{logo_idx}:v]format=rgba,split=2[lga][lgb];"
            f"[lga]scale={bug_w}:{bug_w},{mask},colorchannelmixer=aa={settings.logo_opacity}[bug];"
            f"[lgb]scale={card_w}:{card_w},{mask}[card];"
            f"[0:v][bug]overlay=W-w-40:50:eof_action=repeat[vb];"
            f"[vb]ass=captions.ass[vc];"
            f"[vc]drawbox=x=0:y=0:w=iw:h=ih:color=black@0.6:t=fill:enable='gte(t,{end_start:.2f})'[vd];"
            f"[vd][card]overlay=(W-w)/2:(H-h)/2:enable='gte(t,{end_start:.2f})':eof_action=repeat[vout]"
        )
    else:
        vfilter = "[0:v]ass=captions.ass[vout]"

    amap = "1:a:0"
    afilter = ""
    if music:
        music_idx = idx
        idx += 1
        inputs += ["-stream_loop", "-1", "-i", str(music.resolve())]
        fade_st = max(0.0, duration - 1.0)
        afilter = (
            f"[{music_idx}:a]volume={settings.music_volume},"
            f"afade=t=out:st={fade_st:.2f}:d=1[mus];"
            f"[1:a][mus]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
        amap = "[aout]"

    filter_complex = vfilter + ((";" + afilter) if afilter else "")
    return [
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", amap,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
        "-shortest", "-movflags", "+faststart",
        out_name,
    ]
