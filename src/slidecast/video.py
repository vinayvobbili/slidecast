"""ffmpeg steps: still image + audio -> segment, concat segments, grab a poster.

Every function takes an injectable ``runner`` (defaults to ``subprocess.run``) and
an optional ``ffmpeg`` path, so callers can swap in the bundled binary and tests
can assert the exact command line without invoking ffmpeg.
"""

from __future__ import annotations

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
