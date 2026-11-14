"""Locate an ffmpeg binary without forcing a system install.

Resolution order:
1. ``$SLIDECAST_FFMPEG`` — an explicit path, wins over everything.
2. ``ffmpeg`` on ``$PATH`` — the normal case on a dev box or CI image.
3. The binary bundled with ``imageio-ffmpeg`` (the ``[ffmpeg]`` extra), so a pure
   ``pip install`` with no system package still works.
"""

from __future__ import annotations

import os
import shutil
import subprocess

# A step that should take seconds gets at least this long, then ten times the length of the media it makes.
MIN_TIMEOUT = 60.0
TIMEOUT_FACTOR = 10.0
# For a step whose output length isn't known up front (a concat, a re-encode, an analysis pass).
DEFAULT_TIMEOUT = 1800.0


class FFmpegNotFound(RuntimeError):
    """Raised when no ffmpeg binary can be located by any strategy."""


class FFmpegTimeout(RuntimeError):
    """Raised when an ffmpeg or ffprobe run exceeded its time limit and was killed."""


def timeout_for(seconds: float | None = None) -> float:
    """The time limit for a step that makes ``seconds`` of media: max(60 s, 10x), or 30 min when unknown."""
    if seconds is None:
        return DEFAULT_TIMEOUT
    return max(MIN_TIMEOUT, TIMEOUT_FACTOR * seconds)


def run_ffmpeg(runner, cmd, *, seconds: float | None = None, check: bool = True, **kwargs):
    """Run ``cmd`` through ``runner`` (``subprocess.run`` by default) with a time limit from :func:`timeout_for`.

    ``subprocess.run`` kills the child when the limit passes; that becomes an :class:`FFmpegTimeout` that names
    the command, so a stuck encode fails the render instead of running (and writing) for hours.
    """
    limit = timeout_for(seconds)
    try:
        return (runner or subprocess.run)(list(cmd), check=check, timeout=limit, **kwargs)
    except subprocess.TimeoutExpired as err:
        raise FFmpegTimeout(
            f"{os.path.basename(str(cmd[0]))} ran longer than {limit:.0f} s and was killed: {' '.join(map(str, cmd))}"
        ) from err


def find_ffmpeg() -> str:
    """Return a path to an ffmpeg executable, or raise :class:`FFmpegNotFound`."""
    explicit = os.environ.get("SLIDECAST_FFMPEG")
    if explicit:
        if shutil.which(explicit) or os.path.isfile(explicit):
            return explicit
        raise FFmpegNotFound(f"SLIDECAST_FFMPEG={explicit!r} is not an executable")

    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path

    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # any failure here means "not available"
        pass

    raise FFmpegNotFound(
        "ffmpeg not found. Install it on PATH, set $SLIDECAST_FFMPEG, or "
        "`pip install slidecast[ffmpeg]` to use the bundled binary."
    )
