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


def test_clip_holds_its_last_frame_while_the_narration_finishes(monkeypatch, tmp_path):
    from slidecast import video

    runner = _patch_video_runner(monkeypatch)
    monkeypatch.setattr(video, "probe_duration", lambda media, ffmpeg=None: 4.0)
    renderer = FakeRenderer()
    reel = Reel(width=1280, height=720, tts=SilentTTS(seconds=6.0), renderer=renderer)
    reel.add_clip(tmp_path / "demo.webm", "a long explanation", tail_pad=0.5)
    reel.render(tmp_path / "out.mp4", ffmpeg="ff")

    seg = runner.commands[0]
    assert seg[seg.index("-t") + 1] == "6.500"
    assert "tpad=stop_mode=clone:stop_duration=2.500" in seg[seg.index("-filter_complex") + 1]
    assert not renderer.entered  # clips alone never launch a browser


def test_silent_clip_plays_at_its_own_length(monkeypatch, tmp_path):
    from slidecast import video

    runner = _patch_video_runner(monkeypatch)
    monkeypatch.setattr(video, "probe_duration", lambda media, ffmpeg=None: 4.0)
    reel = Reel(renderer=FakeRenderer())
    reel.add_clip(tmp_path / "demo.webm")
    reel.render(tmp_path / "out.mp4", ffmpeg="ff")
    seg = runner.commands[0]
    assert seg[seg.index("-t") + 1] == "4.000"
    assert "tpad" not in seg[seg.index("-filter_complex") + 1]


def test_clip_with_unmeasured_narration_probes_the_audio(monkeypatch, tmp_path):
    from slidecast import video

    class Mp3TTS:
        def synthesize(self, text, out_path):
            SilentTTS(seconds=1).synthesize("", out_path)
            return None

    probes = {"demo.webm": 3.0, "s001.wav": 5.0}
    runner = _patch_video_runner(monkeypatch)
    monkeypatch.setattr(video, "probe_duration", lambda media, ffmpeg=None: probes[media.name])
    reel = Reel(tts=Mp3TTS(), renderer=FakeRenderer())
    reel.add_clip(tmp_path / "demo.webm", "spoken")
    reel.render(tmp_path / "out.mp4", ffmpeg="ff")
    seg = runner.commands[0]
    assert seg[seg.index("-t") + 1] == "5.000"
