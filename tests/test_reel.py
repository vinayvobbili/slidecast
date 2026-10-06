import subprocess
from pathlib import Path

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


def _mix_commands(runner):
    return [c for c in runner.commands if "-filter_complex" in c and "amix" in c[c.index("-filter_complex") + 1]]


def test_music_alone_is_mixed_with_the_video_copied(monkeypatch, tmp_path):
    from slidecast import Music, video

    runner = _patch_video_runner(monkeypatch)
    monkeypatch.setattr(video, "probe_duration", lambda media, ffmpeg=None: 2.0)
    reel = Reel(renderer=FakeRenderer(), tts=SilentTTS(seconds=1.0), music="bed.mp3")
    reel.add("<h1>a</h1>", "hello")
    reel.render(tmp_path / "out.mp4", ffmpeg="ff")

    seg, cat, mix = runner.commands
    assert "-af" in seg and seg[seg.index("-af") + 1] == "apad"  # no intro -> no lead-in
    assert "concat" in cat and cat[-1].endswith("_concat.mp4")
    assert mix[mix.index("-c:v") + 1] == "copy" and "bed.mp3" in mix
    fc = mix[mix.index("-filter_complex") + 1]
    # The reel knows when the line is spoken (0-2 s), so the bed follows a planned dip.
    assert "sidechaincompress" not in fc
    assert "volume='0.22*(1-0.7488*clip((t--0.250)/0.25,0,1)*clip((2.600-t)/0.6,0,1))':eval=frame" in fc
    assert mix[-1] == str(tmp_path / "out.mp4")
    assert isinstance(reel.music, str)  # coerced per render, not rewritten
    assert Music.of(reel.music).volume == 0.22


def test_intro_sets_the_lead_in_and_fades_go_through_master(monkeypatch, tmp_path):
    from slidecast import Music, video

    runner = _patch_video_runner(monkeypatch)
    probes = {"in.wav": 1.8, "out.wav": 2.0, "_concat.mp4": 5.0}
    monkeypatch.setattr(video, "probe_duration", lambda media, ffmpeg=None: probes[Path(media).name])
    reel = Reel(renderer=FakeRenderer(), tts=SilentTTS(seconds=1.0),
                music=Music("bed.mp3", duck=False), intro="in.wav", outro="out.wav")
    reel.add("<h1>a</h1>", "first", tail_pad=0.2)
    reel.add("<h1>b</h1>", "second")
    reel.render(tmp_path / "out.mp4", ffmpeg="ff", fade_in=0.5, end_hold=1.0, music_volume=0.3)

    first, second = runner.commands[0], runner.commands[1]
    assert first[first.index("-t") + 1] == "3.000"  # 1.8 lead-in + 1.0 voice + 0.2 pad
    assert first[first.index("-af") + 1] == "adelay=1800:all=1,apad"
    assert second[second.index("-af") + 1] == "apad"  # only the first slide waits
    (mix,) = _mix_commands(runner)
    assert mix[mix.index("-c:v") + 1] == "libx264"  # master re-encodes for the fade
    fc = mix[mix.index("-filter_complex") + 1]
    assert "volume=0.3," in fc and "sidechaincompress" not in fc
    assert "adelay=4000:all=1" in fc  # outro ends at 5.0 + 1.0 hold


def test_the_bed_dips_under_each_line_and_the_intro(monkeypatch, tmp_path):
    from slidecast import Music, video

    runner = _patch_video_runner(monkeypatch)
    probes = {"in.wav": 1.5, "s001.wav": 2.0, "s003.wav": 3.0, "_concat.mp4": 9.0}
    monkeypatch.setattr(video, "probe_duration", lambda media, ffmpeg=None: probes[Path(media).name])
    reel = Reel(renderer=FakeRenderer(), tts=SilentTTS(seconds=2.0),
                music=Music("bed.mp3", volume=0.3, duck_db=20), intro="in.wav")
    reel.add("<h1>a</h1>", "first", tail_pad=1.0)   # 0-4.5 s: intro 1.5, voice 2, pause 1
    reel.add("<h1>b</h1>", "", min_duration=1.5)    # 4.5-6 s: silent, no span
    reel.add("<h1>c</h1>", "third", tail_pad=0.5)   # 6-8.5 s: voice 6-9 per its wav
    reel.render(tmp_path / "out.mp4", ffmpeg="ff")

    (mix,) = _mix_commands(runner)
    fc = mix[mix.index("-filter_complex") + 1]
    assert "volume='0.3*(1-0.9000*" in fc  # 20 dB down under the voice
    for start, end in ((0.0, 1.5), (1.5, 3.5), (6.0, 9.0)):  # intro, then each spoken line
        assert f"clip((t-{start - 0.25:.3f})/0.25,0,1)*clip(({end + 0.6:.3f}-t)/0.6,0,1)" in fc
    assert fc.count("clip((t-") == 3


def test_explicit_lead_in_and_loudness_without_music(monkeypatch, tmp_path):
    from slidecast import video

    runner = _patch_video_runner(monkeypatch)
    monkeypatch.setattr(video, "probe_duration", lambda media, ffmpeg=None: 4.0)
    reel = Reel(renderer=FakeRenderer(), tts=SilentTTS(seconds=1.0), lead_in=0.5, loudness=-16)
    reel.add_clip(tmp_path / "demo.webm", "watch")
    reel.render(tmp_path / "out.mp4", ffmpeg="ff")

    clip, _, final = runner.commands
    assert clip[clip.index("-t") + 1] == "4.500"
    assert "tpad=start_mode=clone:start_duration=0.500" in clip[clip.index("-filter_complex") + 1]
    assert final[final.index("-c:v") + 1] == "copy"
    assert "loudnorm=I=-16:" in final[final.index("-filter_complex") + 1]


def test_compose_synthesizes_stings_up_front_and_a_bed_that_outlasts_the_reel(monkeypatch, tmp_path):
    pytest.importorskip("numpy")
    from slidecast import Music, Sting, video
    from slidecast.tts import wav_duration

    runner = _patch_video_runner(monkeypatch)
    # The reel is 3 s, plus a 1 s end-hold from master.
    monkeypatch.setattr(video, "probe_duration",
                        lambda media, ffmpeg=None: wav_duration(media) or 3.0)
    work = tmp_path / "work"
    reel = Reel(renderer=FakeRenderer(), tts=SilentTTS(seconds=1.0), lead_in=0.5,
                music=Music("compose", volume=0.2), intro="compose", outro=Sting("compose", 0.6))
    reel.add("<h1>a</h1>", "hello")
    reel.render(tmp_path / "out.mp4", ffmpeg="ff", workdir=work, end_hold=1.0)

    assert wav_duration(work / "intro_sting.wav") == pytest.approx(5.1, abs=0.01)
    assert wav_duration(work / "outro_sting.wav") == pytest.approx(5.0)
    assert wav_duration(work / "music_bed.wav") == pytest.approx(3.0 + 1.0 + 4.0)
    (mix,) = _mix_commands(runner)
    for name in ("music_bed.wav", "intro_sting.wav", "outro_sting.wav"):
        assert str(work / name) in mix
    assert "compose" not in mix
    assert reel.music.file.name == "compose"  # the spec is untouched; only this render composed


def test_composed_intro_sets_the_default_lead_in(monkeypatch, tmp_path):
    pytest.importorskip("numpy")
    from slidecast.tts import wav_duration

    runner = _patch_video_runner(monkeypatch)
    import slidecast.video as video
    monkeypatch.setattr(video, "probe_duration", lambda media, ffmpeg=None: wav_duration(media) or 2.0)
    reel = Reel(renderer=FakeRenderer(), tts=SilentTTS(seconds=1.0), intro="compose")
    reel.add("<h1>a</h1>", "hello")
    reel.render(tmp_path / "out.mp4", ffmpeg="ff")
    first = runner.commands[0]
    assert first[first.index("-af") + 1] == "adelay=5100:all=1,apad"


def test_compose_without_numpy_fails_before_any_slide_is_built(monkeypatch, tmp_path):
    from slidecast import sound

    runner = _patch_video_runner(monkeypatch)
    monkeypatch.setattr(sound, "np", None)
    renderer = FakeRenderer()
    reel = Reel(renderer=renderer, music="compose")
    reel.add("<h1>a</h1>", "hello")
    with pytest.raises(ImportError, match="slidecast\\[compose\\]"):
        reel.render(tmp_path / "out.mp4", ffmpeg="ff")
    assert not renderer.calls and not runner.commands
