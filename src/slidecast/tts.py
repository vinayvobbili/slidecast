"""Text-to-speech providers.

A provider is anything with::

    synthesize(text: str, out_path: Path) -> float | None

It writes an audio file to ``out_path`` and returns the clip duration in seconds,
or ``None`` if it can't measure it (e.g. it emitted MP3 and you don't want a
probe dependency). When the duration is unknown, the video builder lets the audio
drive the segment length (ffmpeg ``-shortest``) instead of padding to a target.

Three providers ship in the box:

* :class:`KokoroTTS` — any OpenAI-compatible ``/v1/audio/speech`` endpoint
  (Kokoro, OpenAI, LocalAI, …). Defaults to WAV so duration is measurable.
* :class:`GTTSTTS` — Google Translate TTS via the ``gtts`` package (MP3, no
  measurable duration → ``-shortest``).
* :class:`SilentTTS` — a silent track of a fixed length. No dependencies; used
  for silent slides, muted reels, and deterministic tests.

Bring your own by implementing the same one-method shape.
"""

from __future__ import annotations

import re
import struct
import wave
from pathlib import Path
from typing import Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class TTSProvider(Protocol):
    def synthesize(self, text: str, out_path: Path) -> Optional[float]:
        """Write audio for ``text`` to ``out_path``; return its duration or None."""
        ...


def wav_duration(path: Path) -> Optional[float]:
    """Duration of a WAV file in seconds, or None if it isn't a readable WAV."""
    try:
        with wave.open(str(path)) as w:
            rate = w.getframerate()
            return w.getnframes() / float(rate) if rate else None
    except Exception:  # noqa: BLE001
        return None


def apply_phonetic(text: str, rules: Optional[Dict[str, str]]) -> str:
    """Rewrite spoken text by regex so TTS pronounces tricky tokens correctly.

    ``rules`` maps a regex pattern to its spoken replacement, e.g.
    ``{r"\\bSOC\\b": "sock"}`` so "SOC" is said as a word, not spelled out. The
    on-screen slide text is untouched — this only changes the audio.
    """
    if not rules:
        return text
    for pattern, repl in rules.items():
        text = re.sub(pattern, repl, text)
    return text


class SilentTTS:
    """Emit a silent WAV of a fixed length. Pure stdlib, fully deterministic."""

    def __init__(self, seconds: float = 3.0, sample_rate: int = 24000):
        if seconds <= 0:
            raise ValueError("seconds must be > 0")
        self.seconds = float(seconds)
        self.sample_rate = int(sample_rate)

    def synthesize(self, text: str, out_path: Path) -> float:
        n_frames = int(self.seconds * self.sample_rate)
        with wave.open(str(out_path), "w") as w:
            w.setnchannels(1)
            w.setsampwidth(2)  # 16-bit
            w.setframerate(self.sample_rate)
            w.writeframes(struct.pack("<h", 0) * n_frames)
        return self.seconds


class KokoroTTS:
    """Client for any OpenAI-compatible ``/v1/audio/speech`` endpoint.

    Defaults to WAV so the clip duration is measurable (lets the reel pad each
    segment to fit the speech exactly). Set ``response_format="mp3"`` for smaller
    files; duration then reads as unknown and the segment uses ``-shortest``.
    """

    def __init__(
        self,
        url: str = "http://127.0.0.1:8021/v1/audio/speech",
        voice: str = "af_heart",
        model: str = "kokoro",
        response_format: str = "wav",
        timeout: float = 180.0,
        phonetic: Optional[Dict[str, str]] = None,
        session=None,
    ):
        self.url = url
        self.voice = voice
        self.model = model
        self.response_format = response_format
        self.timeout = timeout
        self.phonetic = phonetic
        self._session = session

    def synthesize(self, text: str, out_path: Path) -> Optional[float]:
        import requests

        spoken = apply_phonetic(text, self.phonetic)
        poster = self._session or requests
        resp = poster.post(
            self.url,
            json={
                "model": self.model,
                "voice": self.voice,
                "input": spoken,
                "response_format": self.response_format,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        Path(out_path).write_bytes(resp.content)
        return wav_duration(out_path) if self.response_format == "wav" else None


class GTTSTTS:
    """Google Translate TTS via the ``gtts`` package. Emits MP3 (duration unknown)."""

    def __init__(self, lang: str = "en", tld: str = "com", slow: bool = False,
                 phonetic: Optional[Dict[str, str]] = None):
        self.lang = lang
        self.tld = tld
        self.slow = slow
        self.phonetic = phonetic

    def synthesize(self, text: str, out_path: Path) -> Optional[float]:
        from gtts import gTTS

        spoken = apply_phonetic(text, self.phonetic)
        gTTS(text=spoken, lang=self.lang, tld=self.tld, slow=self.slow).save(str(out_path))
        return None
