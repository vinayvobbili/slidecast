from pathlib import Path

import pytest

from slidecast import build_segment, concat, master, poster
from tests.fakes import FakeRunner


def _filter_complex(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


def test_master_without_music_fades_and_holds():
    runner = FakeRunner()
    master(Path("in.mp4"), Path("out.mp4"),
           fade_in=0.8, fade_out=1.2, end_hold=2.5,
           total_duration=10.0, ffmpeg="ff", runner=runner)
    cmd = runner.commands[0]
    fc = _filter_complex(cmd)
    # total = 10 + 2.5 = 12.5; fade-out starts at 12.5 - 1.2 = 11.3
    assert "tpad=stop_mode=clone:stop_duration=2.500" in fc
    assert "fade=t=in:st=0:d=0.800" in fc
    assert "fade=t=out:st=11.300:d=1.200" in fc
    assert "apad=pad_dur=2.500" in fc
    assert "amix" not in fc  # no music -> no mixer
    assert cmd.count("-i") == 1
    assert "-stream_loop" not in cmd
    assert cmd[-1] == "out.mp4"


def test_master_with_music_loops_attenuates_and_mixes():
    runner = FakeRunner()
    master(Path("in.mp4"), Path("out.mp4"),
           music=Path("bed.mp3"), fade_in=0.8, fade_out=1.2, end_hold=2.5,
           music_volume=0.08, music_fade=1.5,
           total_duration=10.0, ffmpeg="ff", runner=runner)
    cmd = runner.commands[0]
    # music is the second input and is looped infinitely
    assert "-stream_loop" in cmd and cmd[cmd.index("-stream_loop") + 1] == "-1"
    assert cmd.index("in.mp4") < cmd.index("bed.mp3")
    fc = _filter_complex(cmd)
    assert "volume=0.08" in fc
    assert "atrim=0:12.500" in fc  # bed trimmed to total length
    assert "afade=t=out:st=11.000:d=1.500" in fc  # 12.5 - 1.5
    assert "amix=inputs=2:duration=first:dropout_transition=0:normalize=0" in fc
    assert cmd[cmd.index("-map") + 1] == "[v]"


def test_master_can_skip_video_fades():
    runner = FakeRunner()
    master(Path("in.mp4"), Path("out.mp4"),
           fade_in=0, fade_out=0, end_hold=0,
           total_duration=5.0, ffmpeg="ff", runner=runner)
    fc = _filter_complex(runner.commands[0])
    assert "fade=" not in fc
    assert "tpad" not in fc
    assert "format=yuv420p" in fc


def test_build_segment_with_known_duration_pads_to_target():
    runner = FakeRunner()
    build_segment(Path("img.png"), Path("a.wav"), Path("out.mp4"),
                  width=1280, height=720, fps=25, duration=4.2,
                  ffmpeg="ff", runner=runner)
    cmd = runner.commands[0]
    assert cmd[0] == "ff"
    assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "4.200"
    assert "-af" in cmd and cmd[cmd.index("-af") + 1] == "apad"
    assert "-shortest" not in cmd
    assert "scale=1280:720" in cmd
    assert cmd[cmd.index("-r") + 1] == "25"
    assert "+faststart" in cmd
    assert cmd[-1] == "out.mp4"


def test_build_segment_without_duration_uses_shortest():
    runner = FakeRunner()
    build_segment(Path("img.png"), Path("a.wav"), Path("out.mp4"),
                  width=1920, height=1080, duration=None,
                  ffmpeg="ff", runner=runner)
    cmd = runner.commands[0]
    assert "-shortest" in cmd
    assert "-t" not in cmd
    assert "scale=1920:1080" in cmd


def test_concat_writes_listfile_and_copies(tmp_path):
    runner = FakeRunner()
    segs = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for s in segs:
        s.write_bytes(b"x")
    out = concat(segs, tmp_path / "final.mp4", ffmpeg="ff", runner=runner)
    cmd = runner.commands[0]
    assert "concat" in cmd
    assert cmd[cmd.index("-c") + 1] == "copy"
    assert str(out) == str(tmp_path / "final.mp4")


def test_concat_rejects_empty():
    with pytest.raises(ValueError):
        concat([], Path("out.mp4"), ffmpeg="ff", runner=FakeRunner())


def test_poster_grabs_one_frame():
    runner = FakeRunner()
    poster(Path("in.mp4"), Path("p.jpg"), ffmpeg="ff", runner=runner)
    cmd = runner.commands[0]
    assert cmd[cmd.index("-frames:v") + 1] == "1"
    assert cmd[-1] == "p.jpg"
