"""Core data model: a slide is one screen of HTML plus what the voice says over it."""

from __future__ import annotations

from dataclasses import dataclass


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
