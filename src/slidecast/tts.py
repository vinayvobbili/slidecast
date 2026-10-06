"""Text-to-speech providers.

A provider is anything with::

    synthesize(text: str, out_path: Path) -> float | None

It writes an audio file to ``out_path`` and returns the clip duration in seconds,
or ``None`` if it can't measure it (e.g. it emitted MP3 and you don't want a
probe dependency). When the duration is unknown, the video builder lets the audio
drive the segment length (ffmpeg ``-shortest``) instead of padding to a target.

Five providers ship in the box:

* :class:`KokoroTTS` — any OpenAI-compatible ``/v1/audio/speech`` endpoint
  (Kokoro, OpenAI, LocalAI, …). Defaults to WAV so duration is measurable.
* :class:`MLXKokoroTTS` — Kokoro run in-process on Apple Silicon via
  ``mlx-audio``: no server, WAV.
* :class:`GTTSTTS` — Google Translate TTS via the ``gtts`` package (MP3, no
  measurable duration → ``-shortest``).
* :class:`MacSayTTS` — macOS's built-in ``say``: offline, no dependencies, WAV.
* :class:`SilentTTS` — a silent track of a fixed length. No dependencies; used
  for silent slides, muted reels, and deterministic tests.

Bring your own by implementing the same one-method shape.
"""

from __future__ import annotations

import os
import re
import struct
import wave
from array import array
from pathlib import Path
from typing import Callable, Dict, Optional, Protocol, runtime_checkable


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


# Homebrew's espeak-ng, which misaki falls back to for out-of-dictionary words.
# The espeakng-loader wheel's own copy points at its build machine's data path,
# so phonemizing fails unless espeak is pointed at a real install.
HOMEBREW_ESPEAK = {
    "ESPEAK_DATA_PATH": "/opt/homebrew/share/espeak-ng-data",
    "PHONEMIZER_ESPEAK_LIBRARY": "/opt/homebrew/lib/libespeak-ng.dylib",
}


def use_homebrew_espeak(env=None, exists: Callable[[str], bool] = os.path.exists) -> Dict[str, str]:
    """Point espeak at Homebrew's ``espeak-ng`` when it's installed.

    Sets each variable in :data:`HOMEBREW_ESPEAK` that is unset in ``env``
    (default ``os.environ``) and whose path exists, and returns those it set.
    Anything already set is left alone.
    """
    env = os.environ if env is None else env
    applied = {k: v for k, v in HOMEBREW_ESPEAK.items() if not env.get(k) and exists(v)}
    env.update(applied)
    return applied


def _load_mlx_model(model: str):
    use_homebrew_espeak()
    try:
        from mlx_audio.tts.utils import load_model
        import misaki.en  # noqa: F401 — Kokoro's English G2P; fail now, not mid-reel
    except ImportError as e:
        raise ImportError(
            "MLXKokoroTTS needs mlx-audio and misaki (Apple Silicon only): "
            "pip install 'slidecast[mlx]'"
        ) from e
    return load_model(model_path=model)


def _write_wav(path: Path, chunks, sample_rate: int) -> float:
    """Write float audio chunks (-1..1) as a 16-bit mono WAV; return its length."""
    pcm = array("h")
    for chunk in chunks:
        samples = chunk.tolist() if hasattr(chunk, "tolist") else chunk
        pcm.extend(int(max(-1.0, min(1.0, x)) * 32767) for x in samples)
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return len(pcm) / float(sample_rate)


class MLXKokoroTTS:
    """Kokoro run in-process on Apple Silicon via ``mlx-audio`` — no TTS server.

    Install with ``pip install 'slidecast[mlx]'``. The model loads on the first
    line spoken and is reused for the rest of the reel. Writes a 24 kHz WAV, so
    the duration is always known. ``lang_code`` is Kokoro's: "a" American
    English, "b" British. If Homebrew's ``espeak-ng`` is installed it is used for
    out-of-dictionary words (see :func:`use_homebrew_espeak`).

    ``loader`` maps a model id to a loaded model (default ``mlx_audio``'s
    ``load_model``); tests swap in a stub.
    """

    def __init__(
        self,
        voice: str = "af_heart",
        model: str = "mlx-community/Kokoro-82M-bf16",
        speed: float = 1.0,
        lang_code: str = "a",
        phonetic: Optional[Dict[str, str]] = None,
        loader: Optional[Callable[[str], object]] = None,
    ):
        self.voice = voice
        self.model = model
        self.speed = float(speed)
        self.lang_code = lang_code
        self.phonetic = phonetic
        self._loader = loader or _load_mlx_model
        self._model = None

    def synthesize(self, text: str, out_path: Path) -> float:
        if self._model is None:
            self._model = self._loader(self.model)
        chunks, rate = [], 24000
        for result in self._model.generate(
            apply_phonetic(text, self.phonetic), voice=self.voice,
            speed=self.speed, lang_code=self.lang_code,
        ):
            chunks.append(result.audio)
            rate = result.sample_rate
        if not chunks:
            raise RuntimeError(f"Kokoro produced no audio for {text[:40]!r}")
        return _write_wav(Path(out_path), chunks, rate)


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


class MacSayTTS:
    """macOS's built-in speech synthesizer (``say``). Offline, no dependencies, WAV.

    ``voice`` is any name from ``say -v '?'``. The standard voices are serviceable.
    Premium ones such as "Ava (Premium)" or "Zoe (Premium)" sound far better and
    are a free download under System Settings › Accessibility › Spoken Content.
    ``rate`` is words per minute (``say`` defaults to about 175).
    """

    def __init__(self, voice: str = "Samantha", rate: Optional[int] = None,
                 sample_rate: int = 24000, phonetic: Optional[Dict[str, str]] = None,
                 runner=None):
        self.voice = voice
        self.rate = rate
        self.sample_rate = int(sample_rate)
        self.phonetic = phonetic
        self.runner = runner

    def synthesize(self, text: str, out_path: Path) -> Optional[float]:
        import subprocess

        cmd = ["say", "-v", self.voice, "-o", str(out_path),
               "--file-format=WAVE", f"--data-format=LEI16@{self.sample_rate}"]
        if self.rate:
            cmd += ["-r", str(self.rate)]
        # Text goes in on stdin, so nothing in it can be read as an option.
        (self.runner or subprocess.run)(cmd, input=apply_phonetic(text, self.phonetic),
                                        text=True, check=True)
        return wav_duration(out_path)
