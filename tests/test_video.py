from pathlib import Path

import pytest

from slidecast import build_segment, concat, poster
from tests.fakes import FakeRunner


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
