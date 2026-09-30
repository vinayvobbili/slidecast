"""Runs real ffmpeg: a slide and a clip must concat into one playable reel.

Command-line tests can't catch two segments that ffmpeg encodes incompatibly,
which only shows up when the concat demuxer copies them together. Skipped when
no ffmpeg is available.
"""

import shutil
import subprocess

import pytest

from slidecast import Reel, SilentTTS, probe_duration
from slidecast.ffmpeg import FFmpegNotFound, find_ffmpeg

try:
    FFMPEG = find_ffmpeg()
except FFmpegNotFound:
    FFMPEG = None

pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available")


class PngRenderer:
    """Renders every slide as a plain PNG made by ffmpeg, so no browser is needed."""

    def __init__(self, png):
        self.png = png

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def screenshot(self, html, out_path, *, width, height):
        shutil.copy(self.png, out_path)


def _make(args, out):
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", *args, str(out)], check=True)
    return out


def test_slide_and_clip_concat_into_one_reel(tmp_path):
    png = _make(["-f", "lavfi", "-i", "color=c=navy:s=640x400", "-frames:v", "1"], tmp_path / "s.png")
    clip = _make(["-f", "lavfi", "-i", "testsrc=s=320x240:r=30:d=1.5"], tmp_path / "clip.mp4")

    reel = Reel(width=640, height=400, fps=25, tts=SilentTTS(seconds=2.0), renderer=PngRenderer(png))
    reel.add("<h1>title</h1>", "spoken over the slide")
    reel.add_clip(clip, "spoken over the clip, longer than the clip")
    out = reel.render(tmp_path / "out.mp4", ffmpeg=FFMPEG)

    # 2.0s slide + the 1.5s clip held to the 2.0s narration.
    assert probe_duration(out, ffmpeg=FFMPEG) == pytest.approx(4.0, abs=0.15)
    decoded = subprocess.run([FFMPEG, "-v", "error", "-i", str(out), "-f", "null", "-"],
                             capture_output=True, text=True)
    assert decoded.returncode == 0 and not decoded.stderr.strip()
