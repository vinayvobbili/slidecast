import os

import pytest

from slidecast import find_ffmpeg
from slidecast.ffmpeg import FFmpegNotFound


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
