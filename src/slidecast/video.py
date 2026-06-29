"""ffmpeg steps: still image + audio -> segment, concat segments, grab a poster.

Every function takes an injectable ``runner`` (defaults to ``subprocess.run``) and
an optional ``ffmpeg`` path, so callers can swap in the bundled binary and tests
can assert the exact command line without invoking ffmpeg.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from .ffmpeg import find_ffmpeg


def build_segment(
    image: Path,
    audio: Path,
    out: Path,
    *,
    width: int,
    height: int,
    fps: int = 25,
    duration: Optional[float] = None,
    audio_bitrate: str = "192k",
    ffmpeg: Optional[str] = None,
    runner=None,
) -> Path:
    """Render one still image + its audio into an H.264/AAC MP4 segment.

    If ``duration`` is given, the segment is exactly that long and the audio is
    padded with silence to fill it (so narration is never clipped). If it's None,
    the audio drives the length (``-shortest``).
    """
    ffmpeg = ffmpeg or find_ffmpeg()
    runner = runner or subprocess.run
    cmd: List[str] = [
        ffmpeg, "-y", "-loglevel", "error",
        "-loop", "1", "-i", str(image),
        "-i", str(audio),
    ]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}", "-af", "apad"]
    else:
        cmd += ["-shortest"]
    cmd += [
        "-r", str(fps),
        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
        "-vf", f"scale={width}:{height}",
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-movflags", "+faststart",
        str(out),
    ]
    runner(cmd, check=True)
    return out


def concat(
    segments: List[Path],
    out: Path,
    *,
    ffmpeg: Optional[str] = None,
    runner=None,
) -> Path:
    """Concatenate pre-encoded segments losslessly via the concat demuxer."""
    if not segments:
        raise ValueError("concat() needs at least one segment")
    ffmpeg = ffmpeg or find_ffmpeg()
    runner = runner or subprocess.run
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for seg in segments:
            f.write(f"file '{Path(seg).resolve()}'\n")
        list_path = Path(f.name)
    try:
        runner(
            [
                ffmpeg, "-y", "-loglevel", "error",
                "-f", "concat", "-safe", "0",
                "-i", str(list_path),
                "-c", "copy", "-movflags", "+faststart",
                str(out),
            ],
            check=True,
        )
    finally:
        list_path.unlink(missing_ok=True)
    return out


def _ffprobe_for(ffmpeg: str) -> Optional[str]:
    """Best-effort path to an ffprobe that matches ``ffmpeg`` (or one on PATH)."""
    cand = re.sub(r"ffmpeg(\.exe)?$", lambda m: "ffprobe" + (m.group(1) or ""), ffmpeg)
    if cand != ffmpeg and (shutil.which(cand) or os.path.isfile(cand)):
        return cand
    return shutil.which("ffprobe")


def probe_duration(media: Path, *, ffmpeg: Optional[str] = None) -> float:
    """Return the duration of ``media`` in seconds.

    Prefers ``ffprobe`` (matched to the ffmpeg binary, else one on PATH); falls
    back to parsing ``ffmpeg -i`` stderr so a probe-less install still works.
    """
    ffmpeg = ffmpeg or find_ffmpeg()
    probe = _ffprobe_for(ffmpeg)
    if probe:
        res = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(media)],
            capture_output=True, text=True,
        )
        try:
            return float(res.stdout.strip())
        except (ValueError, AttributeError):
            pass
    res = subprocess.run([ffmpeg, "-i", str(media)], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr)
    if m:
        h, mn, s = m.groups()
        return int(h) * 3600 + int(mn) * 60 + float(s)
    raise RuntimeError(f"could not determine duration of {media}")


def master(
    video: Path,
    out: Path,
    *,
    music: Optional[Path] = None,
    fade_in: float = 0.8,
    fade_out: float = 1.2,
    end_hold: float = 2.5,
    music_volume: float = 0.08,
    music_fade: float = 1.5,
    total_duration: Optional[float] = None,
    audio_bitrate: str = "192k",
    ffmpeg: Optional[str] = None,
    runner=None,
) -> Path:
    """Finish a concatenated reel: fade in/out, hold on the last frame, score it.

    A single ffmpeg pass that turns the raw concat into a polished cut:

    * ``fade_in`` / ``fade_out`` — video (and music) fade durations in seconds, so
      the reel eases in and out instead of cutting hard.
    * ``end_hold`` — seconds to freeze the final frame (cloned) after narration
      ends, so the last slide doesn't vanish mid-thought. Narration audio is
      padded with silence across the hold.
    * ``music`` — an optional audio file laid under the whole reel as a bed:
      looped to length, attenuated to ``music_volume`` (linear gain, ~0.08 ≈
      -22 dB), and fading with ``music_fade``. ``normalize=0`` keeps the
      narration from being ducked by the mixer.

    The narration always stays at full level; only the bed is attenuated. The
    total length becomes ``duration(video) + end_hold``.
    """
    ffmpeg = ffmpeg or find_ffmpeg()
    runner = runner or subprocess.run
    end_hold = max(end_hold, 0.0)
    if total_duration is None:
        total_duration = probe_duration(video, ffmpeg=ffmpeg)
    total = total_duration + end_hold

    vchain: List[str] = []
    if end_hold > 0:
        vchain.append(f"tpad=stop_mode=clone:stop_duration={end_hold:.3f}")
    if fade_in > 0:
        vchain.append(f"fade=t=in:st=0:d={fade_in:.3f}")
    if fade_out > 0:
        vchain.append(f"fade=t=out:st={max(total - fade_out, 0.0):.3f}:d={fade_out:.3f}")
    vchain.append("format=yuv420p")
    vfilter = ",".join(vchain)

    cmd: List[str] = [ffmpeg, "-y", "-loglevel", "error"]
    if music is not None:
        cmd += ["-i", str(video), "-stream_loop", "-1", "-i", str(music)]
        afade_out_st = max(total - music_fade, 0.0)
        bed = (
            f"[1:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
            f"volume={music_volume},"
            f"afade=t=in:st=0:d={music_fade:.3f},"
            f"afade=t=out:st={afade_out_st:.3f}:d={music_fade:.3f}[bed]"
        )
        filt = (
            f"[0:v]{vfilter}[v];"
            f"[0:a]apad=pad_dur={end_hold:.3f}[narr];"
            f"{bed};"
            f"[narr][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
        )
    else:
        cmd += ["-i", str(video)]
        filt = f"[0:v]{vfilter}[v];[0:a]apad=pad_dur={end_hold:.3f}[a]"

    cmd += [
        "-filter_complex", filt, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-movflags", "+faststart",
        str(out),
    ]
    runner(cmd, check=True)
    return out


def poster(
    video: Path,
    out: Path,
    *,
    quality: int = 3,
    ffmpeg: Optional[str] = None,
    runner=None,
) -> Path:
    """Write the first frame of ``video`` as a JPEG (a <video> poster image)."""
    ffmpeg = ffmpeg or find_ffmpeg()
    runner = runner or subprocess.run
    runner(
        [
            ffmpeg, "-y", "-loglevel", "error",
            "-i", str(video), "-frames:v", "1", "-q:v", str(quality), str(out),
        ],
        check=True,
    )
    return out
