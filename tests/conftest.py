import os
import signal
import subprocess

import pytest


@pytest.fixture(autouse=True)
def no_default_cache(monkeypatch):
    """Keep tests out of the real ~/.cache/slidecast; the cache tests point SLIDECAST_CACHE at a tmp_path."""
    monkeypatch.setenv("SLIDECAST_CACHE", "off")


def _ffmpeg_children() -> list[int]:
    """Pids of ffmpeg/ffprobe processes whose parent is this test process."""
    # Popen, not run: many tests replace subprocess.run with a fake.
    with subprocess.Popen(["ps", "-A", "-o", "pid=,ppid=,comm="], stdout=subprocess.PIPE, text=True) as ps:
        out = ps.communicate()[0]
    kids = []
    for line in out.splitlines():
        pid, ppid, comm = line.split(None, 2)
        if int(ppid) == os.getpid() and os.path.basename(comm.strip()).startswith(("ffmpeg", "ffprobe")):
            kids.append(int(pid))
    return kids


@pytest.fixture(autouse=True)
def no_ffmpeg_left_running():
    """Kill any ffmpeg a test started and left behind, so an interrupted test never leaves one writing to disk."""
    yield
    for pid in _ffmpeg_children():
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
