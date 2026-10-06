"""The orchestrator: a list of slides (and recorded clips) in, one narrated MP4 out.

A :class:`Reel` ties the three pluggable pieces together — a renderer (HTML ->
PNG), a TTS provider (text -> audio + duration), and the ffmpeg steps (segment +
concat). Per slide it screenshots the HTML, narrates the text, and builds a
segment whose length fits the speech. A clip plays through instead, holding its
last frame if the speech runs longer. Then every segment is concatenated, and
any music bed, intro/outro sting or loudness target is mixed in. A bed or sting
given as "compose" is synthesized (:mod:`slidecast.sound`) into the work directory.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, List, Optional, Union

from . import sound as _sound
from . import video as _video
from .ffmpeg import find_ffmpeg
from .models import Clip, Music, Segment, Slide, Sting
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
        music: An optional bed under the whole reel — a :class:`Music`, or a
            path for its defaults (low, faded, ducked under the voice). "compose"
            synthesizes one that outlasts the reel (needs numpy).
        intro / outro: Optional :class:`Sting` cues (or paths). The intro plays
            from t=0; the outro ends with the reel. "compose" synthesizes the
            stock intro or outro.
        lead_in: Seconds the first slide holds silent before its narration
            starts. None means the intro's length (0 with no intro), so the voice
            never talks over the intro.
        loudness: Integrated-loudness target for the final audio in LUFS (e.g.
            -16 for web). None leaves the level alone.
    """

    width: int = 1920
    height: int = 1080
    fps: int = 25
    tts: TTSProvider = field(default_factory=SilentTTS)
    renderer: Optional[Renderer] = None
    silent_slide_seconds: float = 3.0
    music: Union[Music, str, Path, None] = None
    intro: Union[Sting, str, Path, None] = None
    outro: Union[Sting, str, Path, None] = None
    lead_in: Optional[float] = None
    loudness: Optional[float] = None
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

    @staticmethod
    def _compose_sting(sting: Optional[Sting], path: Path, synth) -> Optional[Sting]:
        """Synthesize a "compose" sting into ``path``; pass any other sting through."""
        if sting is None or not sting.composed:
            return sting
        _sound.write_wav(path, synth())
        return replace(sting, file=path)

    @staticmethod
    def _compose_bed(bed: Music, raw: Path, end_hold: float, work: Path, ffmpeg: str) -> Music:
        """Synthesize a bed that outlasts the reel, so its own closing fade never loops in."""
        length = _video.probe_duration(raw, ffmpeg=ffmpeg) + end_hold + 4.0
        path = work / "music_bed.wav"
        _sound.write_wav(path, _sound.music_bed(length))
        return replace(bed, file=path)

    @staticmethod
    def _note_speech(speech: list, slide, audio: Path, seg: Path, clock: float,
                     delay: float, duration: Optional[float], ffmpeg: str) -> float:
        """Add the segment's spoken span (reel time) to ``speech``; return the next segment's start."""
        if slide.narration.strip():
            start = clock + delay
            speech.append((start, start + _video.probe_duration(audio, ffmpeg=ffmpeg)))
        if duration is None:  # the audio drove its length
            duration = _video.probe_duration(seg, ffmpeg=ffmpeg)
        return clock + duration

    def render(
        self,
        out_path,
        *,
        make_poster: bool = False,
        workdir: Optional[Path] = None,
        ffmpeg: Optional[str] = None,
        on_progress: Optional[ProgressHook] = None,
        music=None,
        fade_in: float = 0.0,
        fade_out: float = 0.0,
        end_hold: float = 0.0,
        music_volume: Optional[float] = None,
        music_fade: Optional[float] = None,
    ) -> Path:
        """Render the whole reel to ``out_path`` (an .mp4). Returns the path.

        If ``make_poster`` is set, also writes ``<stem>_poster.jpg`` next to it.

        When any of ``fade_in`` / ``fade_out`` / ``end_hold`` is non-zero, the
        concatenated reel is run through :func:`video.master` for a finished cut —
        fade in/out and a frozen end-hold, with the reel's music, stings and
        loudness mixed in the same pass. With only music, stings or loudness set,
        :func:`video.mix_music` mixes them and copies the video stream. With none
        of these the reel is concatenated straight to ``out_path``.

        ``music`` overrides :attr:`music` for this render; ``music_volume`` /
        ``music_fade`` override the bed's volume and both its fades.
        """
        if not self.slides:
            raise ValueError("Reel has no slides or clips")
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg = ffmpeg or find_ffmpeg()
        bed = Music.of(music if music is not None else self.music)
        if bed is not None and music_volume is not None:
            bed = replace(bed, volume=music_volume)
        if bed is not None and music_fade is not None:
            bed = replace(bed, fade_in=music_fade, fade_out=music_fade)
        intro, outro = Sting.of(self.intro), Sting.of(self.outro)
        if any(x is not None and x.composed for x in (bed, intro, outro)):
            _sound.require_numpy()  # fail now, not after every slide is built

        keep_work = workdir is not None
        work = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="slidecast_"))
        work.mkdir(parents=True, exist_ok=True)
        # A reel of clips alone never needs a browser.
        needs_browser = any(isinstance(s, Slide) for s in self.slides)
        renderer = (self.renderer or PlaywrightRenderer()) if needs_browser else contextlib.nullcontext()

        segments: List[Path] = []
        total = len(self.slides)
        # When each line is spoken, so a ducking bed can dip under exactly those.
        plan_duck = bed is not None and bed.duck
        speech: List[tuple] = []
        clock = 0.0
        try:
            intro = self._compose_sting(intro, work / "intro_sting.wav", _sound.intro_sting)
            outro = self._compose_sting(outro, work / "outro_sting.wav", _sound.outro_sting)
            lead_in = self.lead_in
            if lead_in is None:
                lead_in = _video.probe_duration(intro.file, ffmpeg=ffmpeg) if intro else 0.0
            with renderer as r:
                for i, slide in enumerate(self.slides, start=1):
                    if on_progress:
                        on_progress(i, total, slide)
                    audio = work / f"s{i:03d}.wav"
                    seg = work / f"s{i:03d}.mp4"
                    # The first segment holds silent through the lead-in (the intro).
                    delay = lead_in if i == 1 else 0.0
                    if isinstance(slide, Clip):
                        duration, hold = self._clip_duration(slide, audio, ffmpeg)
                        _video.build_clip_segment(
                            slide.video, audio, seg,
                            width=self.width, height=self.height, fps=self.fps,
                            duration=duration + delay, hold=hold, delay=delay,
                            ffmpeg=ffmpeg,
                        )
                        segments.append(seg)
                        if plan_duck:
                            clock = self._note_speech(speech, slide, audio, seg, clock,
                                                      delay, duration + delay, ffmpeg)
                        continue
                    png = work / f"s{i:03d}.png"
                    r.screenshot(slide.html, png, width=self.width, height=self.height)
                    duration = self._segment_duration(slide, audio)
                    if duration is not None:
                        duration += delay
                    _video.build_segment(
                        png, audio, seg,
                        width=self.width, height=self.height, fps=self.fps,
                        duration=duration, delay=delay, ffmpeg=ffmpeg,
                    )
                    segments.append(seg)
                    if plan_duck:
                        clock = self._note_speech(speech, slide, audio, seg, clock,
                                                  delay, duration, ffmpeg)
            need_master = fade_in > 0 or fade_out > 0 or end_hold > 0
            need_mix = bool(bed or intro or outro) or self.loudness is not None
            raw = work / "_concat.mp4"
            if need_master or need_mix:
                _video.concat(segments, raw, ffmpeg=ffmpeg)
                if bed is not None and bed.composed:
                    bed = self._compose_bed(bed, raw, end_hold, work, ffmpeg)
            if need_master:
                _video.master(
                    raw, out_path, music=bed, intro=intro, outro=outro,
                    fade_in=fade_in, fade_out=fade_out, end_hold=end_hold,
                    loudness=self.loudness, ffmpeg=ffmpeg,
                    speech=speech if plan_duck else None,
                )
            elif need_mix:
                _video.mix_music(
                    raw, out_path, bed, intro=intro, outro=outro,
                    loudness=self.loudness, ffmpeg=ffmpeg,
                    speech=speech if plan_duck else None,
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
