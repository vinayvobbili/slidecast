import subprocess

import pytest

from slidecast import Reel, SilentTTS
from slidecast.tts import KokoroTTS
from tests.fakes import FakeRenderer, FakeRunner


def _patch_video_runner(monkeypatch):
    """Route slidecast.video's subprocess.run through a recording fake."""
    runner = FakeRunner()
    monkeypatch.setattr(subprocess, "run", runner)
    return runner


def test_render_builds_a_segment_per_slide_then_concats(monkeypatch, tmp_path):
    runner = _patch_video_runner(monkeypatch)
    renderer = FakeRenderer()
    reel = Reel(width=1280, height=720, tts=SilentTTS(seconds=1.0),
                renderer=renderer)
    reel.add("<h1>a</h1>", "first slide")
    reel.add("<h1>b</h1>", "second slide")
    out = reel.render(tmp_path / "out.mp4", ffmpeg="ff")

    assert out == tmp_path / "out.mp4"
    assert renderer.entered and renderer.exited
    assert len(renderer.calls) == 2
    assert renderer.calls[0]["width"] == 1280
    # two build_segment calls + one concat call
    assert len(runner.commands) == 3
    assert "concat" in runner.commands[-1]


def test_silent_slide_uses_min_duration(monkeypatch, tmp_path):
    runner = _patch_video_runner(monkeypatch)
    reel = Reel(renderer=FakeRenderer(), silent_slide_seconds=3.0)
    reel.add("<h1>quiet</h1>", "", min_duration=5.0)
    reel.render(tmp_path / "out.mp4", ffmpeg="ff")
    seg_cmd = runner.commands[0]
    assert seg_cmd[seg_cmd.index("-t") + 1] == "5.000"


def test_tail_pad_added_to_known_duration(monkeypatch, tmp_path):
    runner = _patch_video_runner(monkeypatch)
    reel = Reel(renderer=FakeRenderer(), tts=SilentTTS(seconds=2.0))
    # SilentTTS reports a real duration, so tail_pad applies: 2.0 + 0.5
    reel.add("<h1>x</h1>", "narrated", tail_pad=0.5)
    reel.render(tmp_path / "out.mp4", ffmpeg="ff")
    seg_cmd = runner.commands[0]
    assert seg_cmd[seg_cmd.index("-t") + 1] == "2.500"


def test_unknown_duration_provider_uses_shortest(monkeypatch, tmp_path):
    runner = _patch_video_runner(monkeypatch)

    class UnknownTTS:
        def synthesize(self, text, out_path):
            out_path.write_bytes(b"ID3")
            return None  # can't measure

    reel = Reel(renderer=FakeRenderer(), tts=UnknownTTS())
    reel.add("<h1>x</h1>", "narrated")
    reel.render(tmp_path / "out.mp4", ffmpeg="ff")
    seg_cmd = runner.commands[0]
    assert "-shortest" in seg_cmd
    assert "-t" not in seg_cmd


def test_render_rejects_empty_reel(tmp_path):
    with pytest.raises(ValueError):
        Reel(renderer=FakeRenderer()).render(tmp_path / "out.mp4", ffmpeg="ff")


def test_keep_workdir_retains_intermediates(monkeypatch, tmp_path):
    _patch_video_runner(monkeypatch)
    work = tmp_path / "work"
    reel = Reel(renderer=FakeRenderer(), tts=SilentTTS(seconds=1.0))
    reel.add("<h1>x</h1>", "hi")
    reel.render(tmp_path / "out.mp4", ffmpeg="ff", workdir=work)
    # the stub PNG + silent WAV survive because workdir was explicit
    assert (work / "s001.png").exists()
    assert (work / "s001.wav").exists()
