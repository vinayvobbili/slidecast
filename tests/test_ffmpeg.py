import subprocess
import sys

import pytest

from slidecast import find_ffmpeg
from slidecast.ffmpeg import FFmpegNotFound, FFmpegTimeout, run_ffmpeg, timeout_for


def test_env_override_wins(monkeypatch, tmp_path):
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("SLIDECAST_FFMPEG", str(fake))
    assert find_ffmpeg() == str(fake)


def test_env_override_invalid_raises(monkeypatch):
    monkeypatch.setenv("SLIDECAST_FFMPEG", "/no/such/ffmpeg-xyz")
    with pytest.raises(FFmpegNotFound):
        find_ffmpeg()


def test_falls_back_to_path(monkeypatch):
    monkeypatch.delenv("SLIDECAST_FFMPEG", raising=False)
    monkeypatch.setattr("slidecast.ffmpeg.shutil.which",
                        lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
    assert find_ffmpeg() == "/usr/bin/ffmpeg"


def test_not_found_raises(monkeypatch):
    monkeypatch.delenv("SLIDECAST_FFMPEG", raising=False)
    monkeypatch.setattr("slidecast.ffmpeg.shutil.which", lambda name: None)
    # Make the imageio_ffmpeg fallback fail too.
    import builtins

    real_import = builtins.__import__

    def no_imageio(name, *a, **k):
        if name == "imageio_ffmpeg":
            raise ImportError("nope")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_imageio)
    with pytest.raises(FFmpegNotFound):
        find_ffmpeg()


def test_timeout_scales_with_the_clip_and_has_a_floor():
    assert timeout_for(0) == timeout_for(2) == 60
    assert timeout_for(30) == 300
    assert timeout_for(None) == 1800


def test_run_ffmpeg_passes_a_timeout_to_the_runner():
    seen = {}

    def runner(cmd, **kwargs):
        seen.update(kwargs, cmd=cmd)

    run_ffmpeg(runner, ["ff", "-i", "x"], seconds=30)
    assert seen["timeout"] == 300 and seen["check"] is True and seen["cmd"] == ["ff", "-i", "x"]


def test_a_hung_ffmpeg_is_killed_and_reported(monkeypatch):
    monkeypatch.setattr("slidecast.ffmpeg.MIN_TIMEOUT", 0.3)  # a real child that would sleep for a minute
    with pytest.raises(FFmpegTimeout, match="killed"):
        run_ffmpeg(subprocess.run, [sys.executable, "-c", "import time; time.sleep(60)"], seconds=0)
