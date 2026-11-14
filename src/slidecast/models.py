"""Core data model: a reel is a list of segments — HTML slides and recorded clips —
each with what the voice says over it — plus optional music laid under the whole."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union


@dataclass(frozen=True)
class Theme:
    """The reel's look for the cards it writes itself (the tagline and the closing card).

    Attributes:
        accent_gradient: A CSS gradient for accent text: the tagline and any other line that should stand out.
            Accent text is never pure white; white is for body text only.
        background: The card background.
        body_color: Body text colour (an off-white, not ``#fff``).
    """

    accent_gradient: str = "linear-gradient(90deg, #b9a8ff, #f08cff)"  # lavender to pink
    background: str = "#0e0d1f"
    body_color: str = "#f4f3ff"

    def accent_css(self) -> str:
        """CSS declarations that paint an element's text with the accent gradient."""
        return (f"background: {self.accent_gradient}; -webkit-background-clip: text; "
                "background-clip: text; color: transparent;")

    def accent(self, text: str) -> str:
        """``text`` as an inline span in the accent gradient, for use inside your own slide HTML."""
        return f'<span style="{self.accent_css()}">{text}</span>'


@dataclass
class Slide:
    """One frame of a reel.

    Attributes:
        html: A complete, self-contained HTML document. slidecast does not style
            your slides — it screenshots exactly what you hand it, so the design,
            fonts, and layout are entirely yours.
        narration: What the voice reads over this slide. Empty string => a silent
            slide that holds for ``min_duration`` seconds.
        tail_pad: Seconds of silence appended after the narration so the last word
            is never clipped. Only applied when the narration duration is known.
        min_duration: A floor on the segment length in seconds. For silent slides
            this *is* the duration; for narrated slides the segment is at least
            this long even if the narration is shorter.
    """

    html: str
    narration: str = ""
    tail_pad: float = 0.0
    min_duration: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.html, str) or not self.html.strip():
            raise ValueError("Slide.html must be a non-empty HTML string")
        if self.tail_pad < 0 or self.min_duration < 0:
            raise ValueError("tail_pad and min_duration must be non-negative")


@dataclass
class Clip:
    """A recorded video segment (a screencast, a demo) narrated like a slide.

    The clip plays through; if the narration runs longer, its last frame holds
    until the voice finishes (plus ``tail_pad``). The clip's own audio, if any,
    is replaced by the narration. It is scaled to fit the reel and letterboxed
    rather than stretched.

    Attributes:
        video: Path to the clip (any format ffmpeg reads).
        narration: What the voice reads over it. Empty => the clip plays silent.
        tail_pad: Seconds of silence after the narration, as for :class:`Slide`.
        min_duration: A floor on the segment length; the last frame holds to reach it.
    """

    video: Union[str, Path]
    narration: str = ""
    tail_pad: float = 0.0
    min_duration: float = 0.0

    def __post_init__(self) -> None:
        if not str(self.video).strip():
            raise ValueError("Clip.video must be a path to a video file")
        self.video = Path(self.video)
        if self.tail_pad < 0 or self.min_duration < 0:
            raise ValueError("tail_pad and min_duration must be non-negative")


@dataclass
class Animation:
    """An HTML page whose CSS animations play out as a clip, narrated like a slide.

    The page is rendered frame by frame (every animation paused and stepped to each frame's time, so the result is
    the same on every run), then handled like a :class:`Clip`: if the narration runs longer than ``seconds``, the
    last frame holds until the voice finishes (plus ``tail_pad``).

    Attributes:
        html: A complete, self-contained HTML document. Use CSS animations or transitions started at load (the Web
            Animations API steps them); nothing driven by script timers or ``requestAnimationFrame`` will move.
        narration: What the voice reads over it. Empty => it plays silent.
        seconds: How long the animation runs, from page load.
        tail_pad: Seconds of silence after the narration, as for :class:`Slide`.
        min_duration: A floor on the segment length; the last frame holds to reach it.
    """

    html: str
    narration: str = ""
    seconds: float = 3.0
    tail_pad: float = 0.0
    min_duration: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.html, str) or not self.html.strip():
            raise ValueError("Animation.html must be a non-empty HTML string")
        if self.seconds <= 0:
            raise ValueError("Animation.seconds must be positive")
        if self.tail_pad < 0 or self.min_duration < 0:
            raise ValueError("tail_pad and min_duration must be non-negative")


Segment = Union[Slide, Clip, Animation]

# A Music or Sting ``file`` of "compose" asks the reel to synthesize one (see
# :mod:`slidecast.sound`) into its work directory at render time.
COMPOSE = "compose"
STYLES = ("calm", "upbeat")  # the composed styles; kept in step with sound.STYLES (sound needs numpy)
UPBEAT_DUCK_DB = 18.0


@dataclass
class Music:
    """A music bed laid under the whole reel, below the narration.

    The track is looped to the reel's length, attenuated, and faded in and out.
    With ``duck`` on it also dips while someone speaks (and under any intro/outro
    sting), then swells back in the gaps.

    Attributes:
        file: Path to the track (any format ffmpeg reads), or "compose" to have
            a :class:`Reel` synthesize a bed as long as the reel (needs numpy).
        volume: Gain (a plain multiplier) on the bed in the pauses; 0.22 is about -13 dB.
        fade_in / fade_out: Seconds to fade the bed in at the start and out at
            the end. 0 skips the fade.
        duck: Pull the bed down while the voice is talking.
        style: For a composed bed (and the stings): "calm" (lo-fi, 84 BPM) or "upbeat" (a 124 BPM dance
            bed with builds, kept out of the speech band, and ducked harder by default).
        duck_db: How far it dips under the voice, in dB (12 by default, 18 for the upbeat style). A :class:`Reel` knows
            when each line is spoken, so the bed eases down just before each
            line, holds there, and swells back in the pause after it. Without
            those times (:func:`mix_music` on a finished video) a sidechain
            compressor follows the voice instead, and this is ignored.
    """

    file: Union[str, Path]
    volume: float = 0.22
    fade_in: float = 1.0
    fade_out: float = 2.0
    duck: bool = True
    duck_db: Optional[float] = None
    style: str = "calm"

    def __post_init__(self) -> None:
        if not str(self.file).strip():
            raise ValueError("Music.file must be a path to an audio file")
        self.file = Path(self.file)
        if self.style not in STYLES:
            raise ValueError(f"Music.style must be one of {', '.join(STYLES)}")
        if self.duck_db is None:  # the upbeat bed is busier, so it dips further under the voice
            self.duck_db = UPBEAT_DUCK_DB if self.style == "upbeat" else 12.0
        if min(self.volume, self.fade_in, self.fade_out, self.duck_db) < 0:
            raise ValueError("volume, fade_in, fade_out and duck_db must be non-negative")

    @property
    def composed(self) -> bool:
        """True when ``file`` is "compose": the reel synthesizes it at render time."""
        return str(self.file) == COMPOSE

    @classmethod
    def of(cls, value) -> Optional["Music"]:
        """Coerce a path (or an existing :class:`Music`, or None) into a Music."""
        if value is None or isinstance(value, cls):
            return value
        return cls(value)


@dataclass
class Sting:
    """A short opening or closing cue played once, at full presence, never ducked.

    An intro starts at t=0; an outro is placed so it ends with the reel.

    Attributes:
        file: Path to the sting (any format ffmpeg reads), or "compose" to have
            a :class:`Reel` synthesize the stock intro or outro (needs numpy).
        volume: Gain, a plain multiplier. None (the default) means 0.75 for an intro and 0.7
            for an outro, clearly above the bed.
    """

    file: Union[str, Path]
    volume: Optional[float] = None

    def __post_init__(self) -> None:
        if not str(self.file).strip():
            raise ValueError("Sting.file must be a path to an audio file")
        self.file = Path(self.file)
        if self.volume is not None and self.volume < 0:
            raise ValueError("volume must be non-negative")

    @property
    def composed(self) -> bool:
        """True when ``file`` is "compose": the reel synthesizes it at render time."""
        return str(self.file) == COMPOSE

    @classmethod
    def of(cls, value) -> Optional["Sting"]:
        """Coerce a path (or an existing :class:`Sting`, or None) into a Sting."""
        if value is None or isinstance(value, cls):
            return value
        return cls(value)
