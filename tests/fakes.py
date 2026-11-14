"""Test doubles: a renderer that writes a stub PNG and a runner that records
ffmpeg command lines instead of executing them."""

from __future__ import annotations

from pathlib import Path


class FakeRenderer:
    """A renderer that writes a 1-byte stub file and remembers each call."""

    def __init__(self):
        self.calls = []
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc):
        self.exited = True
        return None

    def screenshot(self, html, out_path, *, width, height):
        self.calls.append({"html": html, "out": Path(out_path),
                           "width": width, "height": height})
        Path(out_path).write_bytes(b"\x89PNG")


class FakeAnimator(FakeRenderer):
    """A renderer that can also step animations: writes a stub PNG per frame."""

    def animate(self, html, out_dir, *, width, height, fps, seconds):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        frames = max(1, round(seconds * fps))
        for k in range(frames):
            (out_dir / f"f{k:05d}.png").write_bytes(b"\x89PNG")
        self.calls.append({"html": html, "animate": True, "frames": frames, "fps": fps, "width": width})
        return frames


class FakeRunner:
    """Stand-in for subprocess.run that captures argv lists, runs nothing.

    With ``touch_output=True`` it creates each command's final argument as an
    empty file, mimicking ffmpeg producing its output so downstream ``stat()``
    calls succeed.
    """

    def __init__(self, touch_output=False):
        self.commands = []
        self.touch_output = touch_output

    def __call__(self, cmd, **kwargs):
        cmd = list(cmd)
        self.commands.append(cmd)
        if self.touch_output and cmd:
            Path(cmd[-1]).write_bytes(b"\x00\x00")
        return None
