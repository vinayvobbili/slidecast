"""Core data model: a reel is a list of segments — HTML slides and recorded clips —
each with what the voice says over it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Union


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
