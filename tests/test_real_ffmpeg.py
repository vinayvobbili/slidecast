"""Runs real ffmpeg: a slide and a clip must concat into one playable reel.

Command-line tests can't catch two segments that ffmpeg encodes incompatibly,
which only shows up when the concat demuxer copies them together. Skipped when
no ffmpeg is available.
"""

import re
import shutil
import statistics
import subprocess

import pytest

from slidecast import Music, Reel, SilentTTS, probe_duration
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


class ToneTTS:
    """'Speaks' every line as a 3 kHz mono tone at 24 kHz, like Kokoro's WAVs: 1 s, or ``seconds``."""

    def __init__(self, seconds=1.0):
        self.seconds = seconds

    def synthesize(self, text, out_path):
        _make(["-f", "lavfi", "-i", f"sine=f=3000:d={self.seconds}:sample_rate=24000", "-ac", "1"], out_path)
        return probe_duration(out_path, ffmpeg=FFMPEG)


def _level(media, start, end, band):
    """Mean volume (dB) of ``media`` between ``start`` and ``end`` s, through ``band``."""
    res = subprocess.run(
        [FFMPEG, "-i", str(media), "-af",
         f"atrim={start}:{end},{band},volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    m = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", res.stderr)
    return float(m.group(1)) if m and m.group(1) != "-inf" else -120.0


def _video_md5(media):
    res = subprocess.run([FFMPEG, "-v", "error", "-i", str(media), "-map", "0:v",
                          "-c", "copy", "-f", "md5", "-"],
                         capture_output=True, text=True, check=True)
    return res.stdout.strip()


BED = "lowpass=f=200,lowpass=f=200"  # the 100 Hz bed alone
VOICE = "highpass=f=2500,highpass=f=2500"  # the 3 kHz voice alone
STING = "highpass=f=400,highpass=f=400,lowpass=f=900,lowpass=f=900"  # the 600 Hz stings


def test_music_bed_ducks_under_the_voice_with_stings_at_each_end(tmp_path):
    png = _make(["-f", "lavfi", "-i", "color=c=navy:s=160x120", "-frames:v", "1"], tmp_path / "s.png")
    bed = _make(["-f", "lavfi", "-i", "sine=f=100:d=1.3"], tmp_path / "bed.wav")  # looped to fit
    sting = _make(["-f", "lavfi", "-i", "sine=f=600:d=0.5"], tmp_path / "sting.wav")

    reel = Reel(width=160, height=120, fps=25, tts=ToneTTS(), renderer=PngRenderer(png),
                music=Music(bed, fade_in=0.2, fade_out=0.3), intro=sting, outro=sting)
    # Lead-in defaults to the 0.5 s intro: voice 0.5-1.5, pause, voice 2.5-3.5, outro 4.0-4.5.
    reel.add("<h1>one</h1>", "first", tail_pad=1.0)
    reel.add("<h1>two</h1>", "second", tail_pad=1.0)
    out = reel.render(tmp_path / "out.mp4", ffmpeg=FFMPEG, workdir=tmp_path / "work")

    assert probe_duration(out, ffmpeg=FFMPEG) == pytest.approx(4.5, abs=0.15)
    decoded = subprocess.run([FFMPEG, "-v", "error", "-i", str(out), "-f", "null", "-"],
                             capture_output=True, text=True)
    assert decoded.returncode == 0 and not decoded.stderr.strip()
    assert "stereo" in subprocess.run([FFMPEG, "-i", str(out)], capture_output=True, text=True).stderr
    # Only the audio was mixed: the video packets are the concat's, byte for byte.
    assert _video_md5(out) == _video_md5(tmp_path / "work" / "_concat.mp4")

    # The intro plays alone; the voice waits for it.
    assert _level(out, 0.05, 0.4, STING) > _level(out, 0.05, 0.4, VOICE) + 20
    assert _level(out, 0.7, 1.3, VOICE) > _level(out, 0.05, 0.4, VOICE) + 20
    # The outro ends the reel, after the last line.
    assert _level(out, 4.1, 4.4, STING) > _level(out, 3.0, 3.4, STING) + 15
    # The bed dips while the voice talks and comes back up in the pause.
    assert _level(out, 2.1, 2.45, BED) > _level(out, 0.8, 1.4, BED) + 6


@pytest.mark.parametrize("mixed", [False, True], ids=["plain", "bed-stings-loudness"])
def test_voice_stays_on_its_slide_across_many_segments(tmp_path, mixed):
    # A long reel (a 20-minute drill) must keep every line on its own slide: no drift that has slides change
    # mid-sentence or the outro play over the last line.
    if mixed:
        pytest.importorskip("numpy")
    png = _make(["-f", "lavfi", "-i", "color=c=navy:s=160x120", "-frames:v", "1"], tmp_path / "s.png")
    extra = dict(music="compose", intro="compose", outro="compose", loudness=-16, lead_in=0.0) if mixed else {}
    reel = Reel(width=160, height=120, fps=25, tts=ToneTTS(), renderer=PngRenderer(png), **extra)
    for n in range(40):
        reel.add(f"<h1>{n}</h1>", f"line {n}", tail_pad=1.0)  # tone 2n..2n+1, quiet 2n+1..2n+2
    out = reel.render(tmp_path / "out.mp4", ffmpeg=FFMPEG)

    # Every line sounds where its slide is, and the quiet after it is still quiet: the last line as clearly as the
    # first (the composed bed and stings leave a little in the voice's band, so compare against the first).
    def contrast(n):
        return _level(out, 2 * n + 0.1, 2 * n + 0.9, VOICE) - _level(out, 2 * n + 1.15, 2 * n + 1.9, VOICE)
    first, middle, last = contrast(0), contrast(20), contrast(38)
    assert first > 10 and middle > first - 4 and last > first - 4, (first, middle, last)


@pytest.mark.parametrize("mixed", [False, True], ids=["plain", "bed-stings-loudness"])
def test_lines_of_any_length_dont_drift(tmp_path, mixed):
    # Kokoro's lines are any length (1.013 s), not a whole number of video frames or AAC frames; what each segment
    # rounds must not add up over a long reel.
    if mixed:
        pytest.importorskip("numpy")
    from slidecast.check import check

    png = _make(["-f", "lavfi", "-i", "color=c=navy:s=160x120", "-frames:v", "1"], tmp_path / "s.png")
    alt = _make(["-f", "lavfi", "-i", "color=c=orange:s=160x120", "-frames:v", "1"], tmp_path / "t.png")
    # The default lead-in: the intro plays out before the first line, as in a drill.
    extra = dict(music="compose", intro="compose", outro="compose", loudness=-16) if mixed else {}

    class Alternating(PngRenderer):
        def screenshot(self, html, out_path, *, width, height):
            shutil.copy(png if "even" in html else alt, out_path)

    reel = Reel(width=160, height=120, fps=25, tts=ToneTTS(seconds=1.013), renderer=Alternating(png), **extra)
    for n in range(80):
        reel.add("<p>even</p>" if n % 2 == 0 else "<p>odd</p>", f"line {n}", tail_pad=0.517)
    out = reel.render(tmp_path / "out.mp4", ffmpeg=FFMPEG)

    cuts = check(out, ffmpeg=FFMPEG)
    assert len(cuts) == 79
    # Changes get the 0.517 s pad (to a 50 ms frame), the last ones as much as the first. Medians, since a composed
    # bed's accent now and then lands in the voice's band; drift would shrink them all.
    early = statistics.median(c.quiet_before for c in cuts[:20])
    late = statistics.median(c.quiet_before for c in cuts[-20:])
    assert 0.4 <= early <= 0.65 and abs(late - early) <= 0.1, (early, late)


def test_loudness_target_lifts_a_quiet_reel(tmp_path):
    from slidecast import mix_music

    quiet = _make(["-f", "lavfi", "-i", "color=c=navy:s=160x120:r=25:d=4",
                   "-f", "lavfi", "-i", "sine=f=440:d=4", "-af", "volume=0.1",
                   "-c:v", "libx264", "-c:a", "aac"], tmp_path / "quiet.mp4")
    out = mix_music(quiet, tmp_path / "loud.mp4", loudness=-16, ffmpeg=FFMPEG)
    res = subprocess.run([FFMPEG, "-i", str(out), "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True, check=True)
    lufs = float(re.findall(r"I:\s*(-?[\d.]+) LUFS", res.stderr)[-1])
    assert lufs == pytest.approx(-16, abs=1.5)
    assert "48000 Hz" in subprocess.run([FFMPEG, "-i", str(out)], capture_output=True, text=True).stderr


def test_composed_bed_and_stings_mix_into_a_reel(tmp_path):
    pytest.importorskip("numpy")
    png = _make(["-f", "lavfi", "-i", "color=c=navy:s=160x120", "-frames:v", "1"], tmp_path / "s.png")

    reel = Reel(width=160, height=120, fps=25, tts=ToneTTS(), renderer=PngRenderer(png),
                music="compose", intro="compose", outro="compose", lead_in=1.0)
    reel.add("<h1>one</h1>", "first", tail_pad=2.0)
    out = reel.render(tmp_path / "out.mp4", ffmpeg=FFMPEG, workdir=tmp_path / "work")

    # 1 s lead-in, 1 s voice, then the 5 s outro starts 0.3 s after the last word.
    assert probe_duration(out, ffmpeg=FFMPEG) == pytest.approx(7.3, abs=0.15)
    assert probe_duration(tmp_path / "work" / "music_bed.wav", ffmpeg=FFMPEG) >= 7.3 + 3.0
    decoded = subprocess.run([FFMPEG, "-v", "error", "-i", str(out), "-f", "null", "-"],
                             capture_output=True, text=True)
    assert decoded.returncode == 0 and not decoded.stderr.strip()
    # Music under the whole reel: the opening second, before the voice, isn't silent.
    assert _level(out, 0.2, 0.9, "anull") > -50
