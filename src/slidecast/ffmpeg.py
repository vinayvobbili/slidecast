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


class FFmpegNotFound(RuntimeError):
    """Raised when no ffmpeg binary can be located by any strategy."""


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
    except Exception:  # noqa: BLE001 — any failure here means "not available"
        pass

    raise FFmpegNotFound(
        "ffmpeg not found. Install it on PATH, set $SLIDECAST_FFMPEG, or "
        "`pip install slidecast[ffmpeg]` to use the bundled binary."
    )
