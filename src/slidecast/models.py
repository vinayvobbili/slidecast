"""Core data model: a reel is a list of segments — HTML slides and recorded clips —
each with what the voice says over it — plus optional music laid under the whole."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union


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


Segment = Union[Slide, Clip]

# A Music or Sting ``file`` of "compose" asks the reel to synthesize one (see
# :mod:`slidecast.sound`) into its work directory at render time.
COMPOSE = "compose"


@dataclass
class Music:
    """A music bed laid under the whole reel, below the narration.

    The track is looped to the reel's length, attenuated, and faded in and out.
    With ``duck`` on it also dips while someone speaks (and under any intro/outro
    sting), then swells back in the gaps.

    Attributes:
        file: Path to the track (any format ffmpeg reads), or "compose" to have
            a :class:`Reel` synthesize a bed as long as the reel (needs numpy).
        volume: Linear gain on the bed in the pauses; 0.22 is about -13 dB.
        fade_in / fade_out: Seconds to fade the bed in at the start and out at
            the end. 0 skips the fade.
        duck: Pull the bed down while the voice is talking.
        duck_db: How far it dips under the voice, in dB. A :class:`Reel` knows
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
    duck_db: float = 12.0

    def __post_init__(self) -> None:
        if not str(self.file).strip():
            raise ValueError("Music.file must be a path to an audio file")
        self.file = Path(self.file)
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
        volume: Linear gain. None (the default) means 0.75 for an intro and 0.7
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
