"""Renderers turn an HTML string into a PNG screenshot at a fixed size.

A renderer is a context manager (so an implementation can launch a browser once
and reuse it across every slide) exposing::

    screenshot(html: str, out_path: Path, *, width: int, height: int) -> None

Two ship in the box:

* :class:`PlaywrightRenderer` — the default. Needs the ``[playwright]`` extra and
  a one-time ``playwright install chromium``. Launches Chromium once per reel.
* :class:`ChromeBinaryRenderer` — drives an existing Chrome/Chromium binary by
  path via ``--headless --screenshot``. No Python browser dep; handy when a
  Chrome is already on the box.

Bring your own by matching the same context-manager + ``screenshot`` shape.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Protocol, Sequence, runtime_checkable


@runtime_checkable
class Renderer(Protocol):
    def __enter__(self) -> "Renderer": ...
    def __exit__(self, *exc) -> None: ...
    def screenshot(self, html: str, out_path: Path, *, width: int, height: int) -> None: ...


class PlaywrightRenderer:
    """Screenshot HTML with headless Chromium via Playwright.

    The browser is launched on ``__enter__`` and reused for every ``screenshot``
    call, so rendering a 20-slide reel pays the startup cost once.
    """

    def __init__(
        self,
        device_scale_factor: int = 1,
        wait_ms: int = 250,
        wait_until: str = "networkidle",
        launch_args: Sequence[str] = ("--force-color-profile=srgb",),
    ):
        self.device_scale_factor = device_scale_factor
        self.wait_ms = wait_ms
        self.wait_until = wait_until
        self.launch_args = list(launch_args)
        self._pw = None
        self._browser = None

    def __enter__(self) -> "PlaywrightRenderer":
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(args=self.launch_args)
        return self

    def __exit__(self, *exc) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._pw is not None:
            self._pw.stop()
            self._pw = None

    def screenshot(self, html: str, out_path: Path, *, width: int, height: int) -> None:
        if self._browser is None:
            raise RuntimeError("PlaywrightRenderer must be used as a context manager")
        page = self._browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=self.device_scale_factor,
        ).new_page()
        try:
            page.set_content(html, wait_until=self.wait_until)
            if self.wait_ms:
                page.wait_for_timeout(self.wait_ms)
            page.screenshot(path=str(out_path))
        finally:
            page.context.close()


class ChromeBinaryRenderer:
    """Screenshot HTML by shelling out to a Chrome/Chromium binary.

    Stateless: writes the HTML to a temp file and runs the binary headless with
    ``--screenshot``. Use when you already have a Chrome on the box and would
    rather not add the Playwright dependency.
    """

    def __init__(
        self,
        chrome: str,
        device_scale_factor: int = 1,
        virtual_time_budget_ms: int = 2000,
        extra_args: Sequence[str] = (),
        runner=subprocess.run,
    ):
        self.chrome = chrome
        self.device_scale_factor = device_scale_factor
        self.virtual_time_budget_ms = virtual_time_budget_ms
        self.extra_args = list(extra_args)
        self.runner = runner

    def __enter__(self) -> "ChromeBinaryRenderer":
        return self

    def __exit__(self, *exc) -> None:
        return None

    def screenshot(self, html: str, out_path: Path, *, width: int, height: int) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
            f.write(html)
            html_path = Path(f.name)
        try:
            cmd = [
                self.chrome, "--headless=new", "--no-sandbox", "--disable-gpu",
                "--hide-scrollbars",
                f"--force-device-scale-factor={self.device_scale_factor}",
                f"--window-size={width},{height}",
                "--default-background-color=00000000",
                f"--virtual-time-budget={self.virtual_time_budget_ms}",
                *self.extra_args,
                f"--screenshot={out_path}", html_path.as_uri(),
            ]
            self.runner(cmd, check=True, capture_output=True)
        finally:
            html_path.unlink(missing_ok=True)
