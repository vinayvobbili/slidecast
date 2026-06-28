"""The orchestrator: a list of slides in, one narrated MP4 out.

A :class:`Reel` ties the three pluggable pieces together — a renderer (HTML ->
PNG), a TTS provider (text -> audio + duration), and the ffmpeg steps (segment +
concat). Per slide it screenshots the HTML, narrates the text, and builds a
segment whose length fits the speech; then it concatenates every segment.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from . import video as _video
from .ffmpeg import find_ffmpeg
from .models import Slide
from .render import PlaywrightRenderer, Renderer
from .tts import SilentTTS, TTSProvider

# Called as on_progress(index, total, slide) before each slide is built.
ProgressHook = Callable[[int, int, Slide], None]


@dataclass
class Reel:
    """A narrated slide reel.

    Args:
        width / height: Output resolution in pixels.
        fps: Output frame rate.
        tts: A text-to-speech provider. Defaults to silent (:class:`SilentTTS`),
            so a reel renders end to end with no audio backend configured.
        renderer: An HTML screenshotter. Defaults to :class:`PlaywrightRenderer`.
        silent_slide_seconds: Hold time for a slide with empty narration when it
            sets no ``min_duration`` of its own.
    """

    width: int = 1920
    height: int = 1080
    fps: int = 25
    tts: TTSProvider = field(default_factory=SilentTTS)
    renderer: Optional[Renderer] = None
    silent_slide_seconds: float = 3.0
    slides: List[Slide] = field(default_factory=list)

    def add(self, html: str, narration: str = "", *,
            tail_pad: float = 0.0, min_duration: float = 0.0) -> Slide:
        """Append a slide and return it (chainable-ish convenience over the list)."""
        slide = Slide(html=html, narration=narration,
                      tail_pad=tail_pad, min_duration=min_duration)
        self.slides.append(slide)
        return slide

    def _segment_duration(self, slide: Slide, audio: Path) -> Optional[float]:
        """Narrate ``slide`` into ``audio`` and return the segment's target length.

        Returns a concrete duration when it's known (so the segment is padded to
        fit), or None to let the audio drive the length (ffmpeg ``-shortest``).
        """
        if not slide.narration.strip():
            seconds = slide.min_duration or self.silent_slide_seconds
            SilentTTS(seconds=seconds).synthesize("", audio)
            return seconds

        narrated = self.tts.synthesize(slide.narration, audio)
        if narrated is None:
            # Provider couldn't measure (e.g. MP3) — audio drives the length.
            return None
        return max(narrated + slide.tail_pad, slide.min_duration)

    def render(
        self,
        out_path,
        *,
        make_poster: bool = False,
        workdir: Optional[Path] = None,
        ffmpeg: Optional[str] = None,
        on_progress: Optional[ProgressHook] = None,
    ) -> Path:
        """Render the whole reel to ``out_path`` (an .mp4). Returns the path.

        If ``make_poster`` is set, also writes ``<stem>_poster.jpg`` next to it.
        """
        if not self.slides:
            raise ValueError("Reel has no slides")
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg = ffmpeg or find_ffmpeg()

        keep_work = workdir is not None
        work = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="slidecast_"))
        work.mkdir(parents=True, exist_ok=True)
        renderer = self.renderer or PlaywrightRenderer()

        segments: List[Path] = []
        total = len(self.slides)
        try:
            with renderer as r:
                for i, slide in enumerate(self.slides, start=1):
                    if on_progress:
                        on_progress(i, total, slide)
                    png = work / f"s{i:03d}.png"
                    audio = work / f"s{i:03d}.wav"
                    seg = work / f"s{i:03d}.mp4"
                    r.screenshot(slide.html, png, width=self.width, height=self.height)
                    duration = self._segment_duration(slide, audio)
                    _video.build_segment(
                        png, audio, seg,
                        width=self.width, height=self.height, fps=self.fps,
                        duration=duration, ffmpeg=ffmpeg,
                    )
                    segments.append(seg)
            _video.concat(segments, out_path, ffmpeg=ffmpeg)
            if make_poster:
                _video.poster(out_path, out_path.with_name(out_path.stem + "_poster.jpg"),
                              ffmpeg=ffmpeg)
        finally:
            if not keep_work:
                shutil.rmtree(work, ignore_errors=True)
        return out_path
