"""Reel.add_animated: an HTML page with CSS animations becomes a clip in the reel."""

import hashlib
import subprocess

import pytest

from slidecast import Animation, PlaywrightRenderer, Reel, SilentTTS, probe_duration
from slidecast.ffmpeg import FFmpegNotFound, find_ffmpeg
from tests.fakes import FakeAnimator, FakeRenderer, FakeRunner

PAGE = """<!doctype html><style>
  body { margin: 0; background: #000 }
  .box { position: absolute; top: 0; left: 0; width: 100px; height: 100px; background: #fff;
         animation: slide 2s linear forwards }
  @keyframes slide { to { left: 300px } }
</style><div class="box"></div>"""


def test_add_animated_validates_and_appends():
    reel = Reel(renderer=FakeAnimator())
    animation = reel.add_animated(PAGE, "words", seconds=2.0, tail_pad=0.2)
    assert reel.slides == [animation] and isinstance(animation, Animation)
    with pytest.raises(ValueError):
        reel.add_animated(PAGE, seconds=0)
    with pytest.raises(ValueError):
        reel.add_animated("  ", seconds=1)


def test_animation_is_stepped_at_the_reels_size_and_fps_then_encoded_as_a_clip(monkeypatch, tmp_path):
    runner = FakeRunner(touch_output=True)
    monkeypatch.setattr(subprocess, "run", runner)
    import slidecast.video as video
    monkeypatch.setattr(video, "probe_duration", lambda media, ffmpeg=None: 3.0)
    renderer = FakeAnimator()
    reel = Reel(width=640, height=400, fps=10, tts=SilentTTS(seconds=1.0), renderer=renderer)
    reel.add_animated(PAGE, "short words", seconds=3.0)
    reel.render(tmp_path / "out.mp4", ffmpeg="ff", cache=False, workdir=tmp_path / "work")

    call = renderer.calls[0]
    assert call["animate"] and call["frames"] == 30 and call["fps"] == 10 and call["width"] == 640
    encode = runner.commands[0]
    assert encode[encode.index("-framerate") + 1] == "10"
    assert encode[encode.index("-i") + 1].endswith("f%05d.png")
    # The animation outlasts its narration, so the segment is the animation's length (3 s).
    segment = runner.commands[1]
    assert segment[segment.index("-t") + 1] == "3.000"


def test_narration_longer_than_the_animation_holds_its_last_frame(monkeypatch, tmp_path):
    runner = FakeRunner(touch_output=True)
    monkeypatch.setattr(subprocess, "run", runner)
    import slidecast.video as video
    monkeypatch.setattr(video, "probe_duration", lambda media, ffmpeg=None: 1.0)
    reel = Reel(width=640, height=400, fps=10, tts=SilentTTS(seconds=4.0), renderer=FakeAnimator())
    reel.add_animated(PAGE, "a long line", seconds=1.0)
    reel.render(tmp_path / "out.mp4", ffmpeg="ff", cache=False)
    segment = runner.commands[1]
    assert segment[segment.index("-t") + 1] == "4.000"
    assert "stop_duration=3.000" in segment[segment.index("-filter_complex") + 1]


def test_a_renderer_without_animate_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", FakeRunner(touch_output=True))
    reel = Reel(renderer=FakeRenderer())
    reel.add_animated(PAGE, seconds=1.0)
    with pytest.raises(TypeError, match="animate"):
        reel.render(tmp_path / "out.mp4", ffmpeg="ff", cache=False)


def _chromium_works():
    try:
        with PlaywrightRenderer(wait_ms=0):
            return True
    except Exception:
        return False


try:
    FFMPEG = find_ffmpeg()
except FFmpegNotFound:
    FFMPEG = None

real = pytest.mark.skipif(FFMPEG is None or not _chromium_works(), reason="needs Chromium and ffmpeg")


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@real
def test_frames_follow_the_animation_clock_and_repeat_exactly(tmp_path):
    with PlaywrightRenderer(wait_ms=0) as r:
        count = r.animate(PAGE, tmp_path / "a", width=400, height=200, fps=10, seconds=2.0)
        r.animate(PAGE, tmp_path / "b", width=400, height=200, fps=10, seconds=2.0)
    assert count == 20
    frames = sorted((tmp_path / "a").glob("f*.png"))
    assert len(frames) == 20
    assert _digest(frames[0]) != _digest(frames[10]) != _digest(frames[19])
    assert [_digest(p) for p in frames] == [_digest(p) for p in sorted((tmp_path / "b").glob("f*.png"))]


@real
def test_an_animated_slide_renders_into_a_playable_reel(tmp_path):
    reel = Reel(width=400, height=200, fps=10, tts=SilentTTS(seconds=1.0))
    reel.add_animated(PAGE, "the box slides across", seconds=2.0)
    out = reel.render(tmp_path / "out.mp4", ffmpeg=FFMPEG, cache=False)
    assert probe_duration(out, ffmpeg=FFMPEG) == pytest.approx(2.0, abs=0.15)
    first = tmp_path / "first.png"
    last = tmp_path / "last.png"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(out), "-frames:v", "1", str(first)], check=True)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-sseof", "-0.2", "-i", str(out), "-update", "1",
                    str(last)], check=True)
    assert _digest(first) != _digest(last)
