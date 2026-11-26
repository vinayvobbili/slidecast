"""join_clips and `slidecast join`: finished clips become one video by copying them, with real ffmpeg on clips made
from its own test sources (so no browser and no voice are needed)."""

import shutil
import subprocess
from pathlib import Path

import pytest

from slidecast import JoinMismatch, find_ffmpeg, join_clips, probe_duration
from slidecast.cli import main
from slidecast.video import check_joinable, probe_streams

try:
    FFMPEG = find_ffmpeg()
except Exception:
    FFMPEG = None

pytestmark = pytest.mark.skipif(FFMPEG is None or shutil.which("ffprobe") is None, reason="ffmpeg or ffprobe not found")


def make_clip(path: Path, seconds: float, *, size="320x180", rate=25, hz=48000, tone=440) -> Path:
    """A clip of ffmpeg's test picture and a tone, h264 and AAC like a rendered reel."""
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}",
                    "-f", "lavfi", "-i", f"sine=frequency={tone}:sample_rate={hz}", "-t", str(seconds),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)
    return path


def packet_sizes(path: Path, stream: str) -> list[int]:
    """The byte size of every packet of the first video ("v") or audio ("a") stream: what was encoded."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", f"{stream}:0", "-show_entries", "packet=size",
                          "-of", "csv=p=0", str(path)], check=True, capture_output=True, text=True).stdout
    return [int(line.split(",")[0]) for line in out.splitlines() if line]


class Spy:
    """subprocess.run that keeps each command, so a test can see what ffmpeg was asked to do."""

    def __init__(self):
        self.commands = []

    def __call__(self, cmd, **kw):
        self.commands.append(list(cmd))
        return subprocess.run(cmd, **kw)


@pytest.fixture
def clips(tmp_path):
    return [make_clip(tmp_path / "a.mp4", 2.0, tone=440), make_clip(tmp_path / "b.mp4", 3.0, tone=660)]


def test_clips_are_joined_by_copying_so_the_picture_and_sound_are_the_clips_own(clips, tmp_path):
    spy = Spy()
    out = join_clips(clips, tmp_path / "joined.mp4", ffmpeg=FFMPEG, runner=spy)
    assert probe_duration(out, ffmpeg=FFMPEG) == pytest.approx(5.0, abs=0.2)
    cmd = spy.commands[0]
    assert cmd[cmd.index("-c") + 1] == "copy" and "libx264" not in cmd and "aac" not in cmd  # nothing is encoded
    # the encoded packets are exactly the clips', one after the other: the video was never decoded and drawn again.
    # (Only the first packet of each clip, a keyframe, gains the stream's parameter sets, a few dozen bytes.)
    first, second = packet_sizes(clips[0], "v"), packet_sizes(clips[1], "v")
    joined = packet_sizes(out, "v")
    assert len(joined) == len(first) + len(second)
    assert joined[1:len(first)] == first[1:] and joined[len(first) + 1:] == second[1:]
    assert 0 <= joined[0] - first[0] < 100 and 0 <= joined[len(first)] - second[0] < 100
    assert packet_sizes(out, "a")[:3] == packet_sizes(clips[0], "a")[:3]
    assert probe_streams(out, ffmpeg=FFMPEG) == probe_streams(clips[0], ffmpeg=FFMPEG)


def test_a_clip_that_differs_is_named_with_the_field_and_nothing_is_written(clips, tmp_path):
    small = make_clip(tmp_path / "small.mp4", 1.0, size="160x90")
    out = tmp_path / "joined.mp4"
    with pytest.raises(JoinMismatch) as raised:
        join_clips([*clips, small], out, ffmpeg=FFMPEG)
    message = str(raised.value)
    assert "small.mp4: width is 160, a.mp4 has 320" in message and "height is 90" in message
    assert not out.exists()
    lowrate = make_clip(tmp_path / "lowrate.mp4", 1.0, hz=44100)
    assert check_joinable([clips[0], lowrate], ffmpeg=FFMPEG) == ["lowrate.mp4: sample rate is 44100, a.mp4 has 48000"]
    slow = make_clip(tmp_path / "slow.mp4", 1.0, rate=30)
    assert check_joinable([clips[0], slow], ffmpeg=FFMPEG) == ["slow.mp4: frame rate is 30/1, a.mp4 has 25/1"]
    assert check_joinable(clips, ffmpeg=FFMPEG) == []


def test_the_chapters_file_lists_each_clips_start_and_name(clips, tmp_path):
    join_clips(clips, tmp_path / "joined.mp4", ffmpeg=FFMPEG, chapters=tmp_path / "chapters.txt",
               names=["Opening", "Topic 1. Widgets"])
    assert (tmp_path / "chapters.txt").read_text().splitlines() == ["0:00:00 Opening", "0:00:02 Topic 1. Widgets"]
    join_clips(clips, tmp_path / "again.mp4", ffmpeg=FFMPEG, chapters=tmp_path / "stems.txt")
    assert (tmp_path / "stems.txt").read_text().splitlines()[0] == "0:00:00 a"


def test_join_needs_at_least_one_clip():
    with pytest.raises(ValueError, match="at least one"):
        join_clips([], Path("x.mp4"), ffmpeg=FFMPEG)


def test_the_join_command_copies_the_clips_and_reports_a_mismatch(clips, tmp_path, capsys):
    out = tmp_path / "joined.mp4"
    assert main(["join", str(out), *map(str, clips), "--chapters", str(tmp_path / "c.txt")]) == 0
    assert "joined 2 clips" in capsys.readouterr().out and probe_duration(out, ffmpeg=FFMPEG) > 4.5
    small = make_clip(tmp_path / "small.mp4", 1.0, size="160x90")
    other = tmp_path / "other.mp4"
    assert main(["join", str(other), str(clips[0]), str(small)]) == 1
    assert "small.mp4: width is 160" in capsys.readouterr().err and not other.exists()
    with pytest.raises(SystemExit):
        main(["join", str(other), str(tmp_path / "missing.mp4")])
