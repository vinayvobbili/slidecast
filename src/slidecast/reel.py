"""The orchestrator: a list of slides (and recorded clips) in, one narrated MP4 out.

A :class:`Reel` ties the three pluggable pieces together — a renderer (HTML ->
PNG), a TTS provider (text -> audio + duration), and the ffmpeg steps (segment +
concat). Per slide it screenshots the HTML, narrates the text, and builds a
segment whose length fits the speech. A clip plays through instead, holding its
last frame if the speech runs longer. Then every segment is concatenated.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from . import video as _video
from .ffmpeg import find_ffmpeg
from .models import Clip, Segment, Slide
from .render import PlaywrightRenderer, Renderer
from .tts import SilentTTS, TTSProvider

# Called as on_progress(index, total, segment) before each segment is built.
ProgressHook = Callable[[int, int, Segment], None]


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
    slides: List[Segment] = field(default_factory=list)

    def add(self, html: str, narration: str = "", *,
            tail_pad: float = 0.0, min_duration: float = 0.0) -> Slide:
        """Append a slide and return it (chainable-ish convenience over the list)."""
        slide = Slide(html=html, narration=narration,
                      tail_pad=tail_pad, min_duration=min_duration)
        self.slides.append(slide)
        return slide

    def add_clip(self, video, narration: str = "", *,
                 tail_pad: float = 0.0, min_duration: float = 0.0) -> Clip:
        """Append a recorded clip (e.g. a screencast) narrated like a slide."""
        clip = Clip(video=video, narration=narration,
                    tail_pad=tail_pad, min_duration=min_duration)
        self.slides.append(clip)
        return clip

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

    def _clip_duration(self, clip: Clip, audio: Path, ffmpeg: str) -> tuple:
        """Narrate ``clip`` into ``audio``; return (segment length, last-frame hold)."""
        length = _video.probe_duration(clip.video, ffmpeg=ffmpeg)
        if clip.narration.strip():
            spoken = self.tts.synthesize(clip.narration, audio)
            if spoken is None:
                spoken = _video.probe_duration(audio, ffmpeg=ffmpeg)
            spoken += clip.tail_pad
        else:
            spoken = 0.0
            SilentTTS(seconds=max(length, clip.min_duration)).synthesize("", audio)
        duration = max(length, spoken, clip.min_duration)
        return duration, duration - length

    def render(
        self,
        out_path,
        *,
        make_poster: bool = False,
        workdir: Optional[Path] = None,
        ffmpeg: Optional[str] = None,
        on_progress: Optional[ProgressHook] = None,
        music: Optional[Path] = None,
        fade_in: float = 0.0,
        fade_out: float = 0.0,
        end_hold: float = 0.0,
        music_volume: float = 0.08,
        music_fade: float = 1.5,
    ) -> Path:
        """Render the whole reel to ``out_path`` (an .mp4). Returns the path.

        If ``make_poster`` is set, also writes ``<stem>_poster.jpg`` next to it.

        When ``music`` is given or any of ``fade_in`` / ``fade_out`` / ``end_hold``
        is non-zero, the concatenated reel is run through :func:`video.master` for
        a finished cut — fade in/out, a frozen end-hold, and an optional looped,
        attenuated music bed. With all of them at their defaults the reel is
        concatenated straight to ``out_path`` (the original behaviour).
        """
        if not self.slides:
            raise ValueError("Reel has no slides or clips")
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg = ffmpeg or find_ffmpeg()

        keep_work = workdir is not None
        work = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="slidecast_"))
        work.mkdir(parents=True, exist_ok=True)
        # A reel of clips alone never needs a browser.
        needs_browser = any(isinstance(s, Slide) for s in self.slides)
        renderer = (self.renderer or PlaywrightRenderer()) if needs_browser else contextlib.nullcontext()

        segments: List[Path] = []
        total = len(self.slides)
        try:
            with renderer as r:
                for i, slide in enumerate(self.slides, start=1):
                    if on_progress:
                        on_progress(i, total, slide)
                    audio = work / f"s{i:03d}.wav"
                    seg = work / f"s{i:03d}.mp4"
                    if isinstance(slide, Clip):
                        duration, hold = self._clip_duration(slide, audio, ffmpeg)
                        _video.build_clip_segment(
                            slide.video, audio, seg,
                            width=self.width, height=self.height, fps=self.fps,
                            duration=duration, hold=hold, ffmpeg=ffmpeg,
                        )
                        segments.append(seg)
                        continue
                    png = work / f"s{i:03d}.png"
                    r.screenshot(slide.html, png, width=self.width, height=self.height)
                    duration = self._segment_duration(slide, audio)
                    _video.build_segment(
                        png, audio, seg,
                        width=self.width, height=self.height, fps=self.fps,
                        duration=duration, ffmpeg=ffmpeg,
                    )
                    segments.append(seg)
            need_master = bool(music) or fade_in > 0 or fade_out > 0 or end_hold > 0
            if need_master:
                raw = work / "_concat.mp4"
                _video.concat(segments, raw, ffmpeg=ffmpeg)
                _video.master(
                    raw, out_path,
                    music=Path(music) if music else None,
                    fade_in=fade_in, fade_out=fade_out, end_hold=end_hold,
                    music_volume=music_volume, music_fade=music_fade,
                    ffmpeg=ffmpeg,
                )
            else:
                _video.concat(segments, out_path, ffmpeg=ffmpeg)
            if make_poster:
                # Past the fade-in, or the poster is a dark frame.
                _video.poster(out_path, out_path.with_name(out_path.stem + "_poster.jpg"),
                              at=fade_in + 0.2 if fade_in > 0 else 0.0, ffmpeg=ffmpeg)
        finally:
            if not keep_work:
                shutil.rmtree(work, ignore_errors=True)
        return out_path
