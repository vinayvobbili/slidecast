"""Check a rendered reel's timing: how much quiet each slide change gets before and after it.

    slidecast check out.mp4               # every slide change, with the quiet either side
    slidecast check out.mp4 --min-gap 0.6 # exit 1 when a change comes less than 0.6 s after the voice

A slide change is a scene change in the video. The voice is found in its own band (1-4 kHz), in 50 ms frames, so
a music bed that comes back up between lines doesn't hide the pauses: a frame counts as speech when it is within
``drop`` dB of the reel's loud frames. A change with little quiet before it cuts the line off; a reel whose quiet
before each change shrinks from start to end has its voice drifting later than its slides.
"""

from __future__ import annotations

import math
import re
import subprocess
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from .ffmpeg import find_ffmpeg

HOP = 0.05          # seconds per level frame
RATE = 8000         # the speech band fits well under 4 kHz
BAND = (1000, 4000)
DROP = 18.0         # dB under the loud frames that still counts as speech
PEAK = 10.0         # dB under the loud frames that a burst of speech must reach (a bed's swell doesn't)
JOIN = 4            # speech frames this few (0.2 s) apart or closer belong to one burst
SCENE = 0.05       # ffmpeg's scene score for a slide change


@dataclass(frozen=True)
class Cut:
    at: float                # seconds into the reel
    quiet_before: float      # since the last speech frame before it
    quiet_after: float       # until the first speech frame after it


def slide_changes(video: Path, *, ffmpeg: Optional[str] = None, scene: float = SCENE) -> List[float]:
    ffmpeg = ffmpeg or find_ffmpeg()
    res = subprocess.run([ffmpeg, "-v", "info", "-i", str(video), "-an", "-vf",
                          f"select='gt(scene,{scene})',showinfo", "-f", "null", "-"],
                         capture_output=True, text=True, check=True)
    return [float(t) for t in re.findall(r"pts_time:([0-9.]+)", res.stderr)]


def speech_levels(video: Path, *, ffmpeg: Optional[str] = None, band=BAND, hop: float = HOP) -> List[float]:
    """dB of the voice's band in each ``hop``-second frame."""
    ffmpeg = ffmpeg or find_ffmpeg()
    res = subprocess.run([ffmpeg, "-v", "error", "-i", str(video), "-vn", "-af",
                          f"highpass=f={band[0]},highpass=f={band[0]},lowpass=f={band[1]},lowpass=f={band[1]}",
                          "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
                         capture_output=True, check=True)
    pcm = array("h")
    pcm.frombytes(res.stdout[: len(res.stdout) // 2 * 2])
    size = int(RATE * hop)
    levels = []
    for i in range(0, len(pcm) - size + 1, size):
        frame = pcm[i:i + size]
        power = sum(x * x for x in frame) / size
        levels.append(10 * math.log10(power / 32768 ** 2) if power else -120.0)
    return levels


def speech(levels: Sequence[float], drop: float = DROP, peak: float = PEAK, join: int = JOIN) -> List[bool]:
    """Which frames are speech: within ``drop`` dB of the loud ones (the 95th percentile), in a burst that
    gets within ``peak`` dB of them. Frames less than ``join`` apart make one burst. A music bed that swells
    over a long silent hold crosses the ``drop`` line but never reaches the ``peak`` one; a voice does."""
    if not levels:
        return []
    loud = sorted(levels)[int(0.95 * (len(levels) - 1))]
    above = [x > loud - drop for x in levels]
    out = [False] * len(levels)
    i = 0
    while i < len(levels):
        if not above[i]:
            i += 1
            continue
        end = i  # the burst runs to the last frame above the line before a gap of ``join`` frames
        for j in range(i, len(levels)):
            if above[j]:
                end = j
            elif j - end >= join:
                break
        if max(levels[i:end + 1]) > loud - peak:
            out[i:end + 1] = above[i:end + 1]
        i = end + 1
    return out


def level_rows(levels: Sequence[float], start: float, end: float, talking: Sequence[bool],
               hop: float = HOP) -> List[str]:
    """One printable row per frame from ``start`` to ``end`` seconds: time, dB, a bar, and S where it counts as speech."""
    first, last = max(int(start / hop + 1e-6), 0), min(int(end / hop + 1e-6), len(levels))
    return [f"{i * hop:8.2f} s {levels[i]:7.1f} dB {'#' * max(int((levels[i] + 80) / 2), 0):<40} {'S' if talking[i] else ''}"
            for i in range(first, last)]


def cuts(changes: Sequence[float], talking: Sequence[bool], hop: float = HOP) -> List[Cut]:
    out = []
    for at in changes:
        # Whole frames only: those ending by the change, and those starting from it (0.30 / 0.05 is 5.999...).
        i = min(math.floor(at / hop + 1e-6), len(talking))
        k = min(math.ceil(at / hop - 1e-6), len(talking))
        before = next((i - j - 1 for j in range(i - 1, -1, -1) if talking[j]), i)
        after = next((j - k for j in range(k, len(talking)) if talking[j]), len(talking) - k)
        out.append(Cut(at, round(before * hop, 2), round(after * hop, 2)))
    return out


def check(video: Path, *, ffmpeg: Optional[str] = None, drop: float = DROP) -> List[Cut]:
    return cuts(slide_changes(video, ffmpeg=ffmpeg), speech(speech_levels(video, ffmpeg=ffmpeg), drop))
