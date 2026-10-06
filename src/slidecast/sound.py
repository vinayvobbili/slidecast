"""Compose a music bed and intro/outro stings from scratch — nothing to license.

Everything here is synthesized with numpy (no samples), so the audio is yours
to use anywhere:

* :func:`music_bed` — a soft lo-fi loop: Fmaj7 – G6 – Em7 – Am7 on a pad, an
  electric-piano arpeggio, bass, and a soft kick and shaker from the second
  bar, all through a little reverb.
* :func:`intro_sting` — a rising swoosh that lands on a bright bell chord with
  a low boom at 1.0 s, then rings out.
* :func:`outro_sting` — descending bells resolving on a wide Cmaj7.
* :func:`compose` — writes all three as WAVs into a directory.

The synths return float stereo arrays of shape ``(samples, 2)`` at
:data:`SAMPLE_RATE`; :func:`write_wav` normalizes one to a 16-bit stereo WAV.
numpy is an optional extra: ``pip install 'slidecast[compose]'``.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Dict

try:
    import numpy as np
except ImportError:  # the [compose] extra isn't installed
    np = None

SAMPLE_RATE = 44100
_SR = SAMPLE_RATE


def require_numpy() -> None:
    """Raise a clear ImportError when the [compose] extra (numpy) is missing."""
    if np is None:
        raise ImportError("composing music needs numpy: pip install 'slidecast[compose]'")


def _hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def _t(dur):
    return np.arange(int(dur * _SR)) / _SR


def _env(n, a, r, sustain=1.0):
    """Attack/release envelope over n samples (a, r in seconds)."""
    e = np.full(n, sustain)
    na, nr = min(int(a * _SR), n), min(int(r * _SR), n)
    e[:na] = np.linspace(0, sustain, na)
    if nr:
        e[-nr:] *= np.linspace(1, 0, nr)
    return e


def _lowpass(x, cutoff):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / _SR)
    X *= 1 / np.sqrt(1 + (f / cutoff) ** 4)
    return np.fft.irfft(X, len(x))


def _reverb(x, seconds=1.8, mix=0.3, seed=0):
    """Convolve with a decaying-noise impulse response (stereo out)."""
    r = np.random.default_rng(seed)
    n = int(seconds * _SR)
    decay = np.exp(-np.linspace(0, 7, n))
    outs = []
    for _ in range(2):
        ir = r.standard_normal(n) * decay
        ir = _lowpass(ir, 5000)
        ir /= np.sqrt((ir ** 2).sum())
        size = len(x) + n
        wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x) + n]
        dry = np.concatenate([x, np.zeros(n)])
        outs.append((1 - mix) * dry + mix * wet)
    return np.stack(outs, axis=1)


def _pad_voice(rng, freq, dur, detune=0.003):
    t = _t(dur)
    s = np.zeros_like(t)
    for d in (-detune, 0, detune):
        f = freq * (1 + d)
        for k, amp in ((1, 1.0), (2, 0.35), (3, 0.15), (4, 0.06)):
            s += amp * np.sin(2 * np.pi * f * k * t + rng.uniform(0, 6.28))
    return _lowpass(s, 1400) * _env(len(t), 0.6, 0.9)


def _keys(freq, dur=1.2):
    """Soft electric-piano pluck."""
    t = _t(dur)
    s = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(2 * np.pi * 2 * freq * t) * np.exp(-t * 6)
    return s * np.exp(-t * 3.2) * _env(len(t), 0.004, 0.05)


def _bell(freq, dur=2.5):
    t = _t(dur)
    s = sum(a * np.sin(2 * np.pi * freq * m * t) * np.exp(-t * d)
            for m, a, d in ((1, 1.0, 2.2), (2.0, 0.5, 3.5), (3.01, 0.25, 5), (4.2, 0.12, 7)))
    return s * _env(len(t), 0.002, 0.2)


def _kick(dur=0.35):
    t = _t(dur)
    f = 45 + 70 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(f) / _SR) * np.exp(-t * 9)


def _shaker(rng, dur=0.08):
    n = rng.standard_normal(int(dur * _SR))
    n = n - _lowpass(n, 6000)  # keep the highs
    return n * np.exp(-_t(dur) * 45)


def _place(buf, sig, at):
    i = int(at * _SR)
    j = min(len(buf), i + len(sig))
    if i < len(buf):
        buf[i:j] += sig[: j - i]


def music_bed(length: float = 95.0, bpm: float = 84, seed: int = 7):
    """A lo-fi bed ``length`` seconds long, as a ``(samples, 2)`` float array.

    It fades in over 1.5 s and out over its last 3 s, so when it loops under a
    reel, make it longer than the reel (by 3 s or more) or the fade dips.
    ``seed`` picks the pad's phases and the shaker's noise; the same seed gives
    the same audio.
    """
    require_numpy()
    if length <= 0 or bpm <= 0:
        raise ValueError("length and bpm must be positive")
    rng = np.random.default_rng(seed)
    beat = 60 / bpm
    bar = 4 * beat
    # Fmaj7 – G6 – Em7 – Am7, voiced around middle C
    chords = [[53, 57, 60, 64], [55, 59, 62, 64], [52, 55, 59, 62], [57, 60, 64, 67]]
    roots = [41, 43, 40, 45]
    pad = np.zeros(int((length + 3) * _SR))
    ep, bass, drums = pad.copy(), pad.copy(), pad.copy()
    nbars = int(np.ceil(length / bar))
    for b in range(nbars):
        c, root, at = chords[b % 4], roots[b % 4], b * bar
        for note in c:
            _place(pad, _pad_voice(rng, _hz(note), bar + 0.9), at)
        _place(bass, np.sin(2 * np.pi * _hz(root) * _t(bar)) * _env(int(bar * _SR), 0.05, 0.4), at)
        # a gentle arpeggio on eighth notes, sparser every other bar
        pattern = [0, 2, 1, 3, 2, 1, 3, 2] if b % 2 == 0 else [0, None, 2, None, 3, None, 1, None]
        for k, idx in enumerate(pattern):
            if idx is not None:
                _place(ep, _keys(_hz(c[idx] + 12)) * (0.9 if k % 2 == 0 else 0.6), at + k * beat / 2)
        if b >= 1:  # drums come in after the first bar
            _place(drums, _kick(), at)
            _place(drums, _kick() * 0.7, at + 2.5 * beat)
            for k in range(8):
                _place(drums, _shaker(rng) * (0.5 if k % 2 else 0.25), at + k * beat / 2)
    mono = 0.18 * pad + 0.22 * ep + 0.35 * bass
    stereo = _reverb(mono, 2.2, 0.35, seed=1)[: len(pad)]
    stereo += 0.25 * np.stack([drums, drums], axis=1)
    stereo = stereo[: int(length * _SR)]
    return stereo * _env(len(stereo), 1.5, 3.0)[:, None]


def intro_sting(seed: int = 7):
    """An opening cue, 5.1 s: a swoosh rising to a bell-chord hit at 1.0 s, then its ring-out."""
    require_numpy()
    rng = np.random.default_rng(seed)
    dur = 2.6
    t = _t(dur)
    # rising swoosh: noise through a sweeping filter, swelling in
    noise = rng.standard_normal(len(t))
    sweep = np.zeros_like(noise)
    for k, seg in enumerate(np.array_split(np.arange(len(t)), 40)):
        cut = 300 + (k / 40) ** 2 * 7000
        sweep[seg] = _lowpass(noise, cut)[seg]
    swell = np.clip(t / 1.0, 0, 1) ** 2 * np.exp(-np.clip(t - 1.0, 0, None) * 6)
    out = np.zeros(int((dur + 2.5) * _SR))
    _place(out, 0.35 * sweep * swell, 0)
    # the hit: a bright Cmaj9 bell chord plus a soft low boom
    for m in (60, 64, 67, 71, 74):
        _place(out, 0.22 * _bell(_hz(m + 12), 3.0), 1.0)
    _place(out, 0.6 * _kick(0.8), 1.0)
    _place(out, 0.3 * np.sin(2 * np.pi * _hz(36) * _t(1.5)) * np.exp(-_t(1.5) * 3), 1.0)
    return _reverb(out, 2.0, 0.4, seed=2)[: len(out)]


def outro_sting():
    """A closing cue, 5.0 s: G–E–C–G bells falling onto a wide Cmaj7 at 0.75 s."""
    require_numpy()
    out = np.zeros(int(5.0 * _SR))
    for k, m in enumerate((79, 76, 72, 67)):  # G–E–C–G descending
        _place(out, 0.25 * _bell(_hz(m), 2.0), k * 0.16)
    for m in (48, 60, 64, 67, 71, 76):  # resolve on a wide Cmaj7
        _place(out, 0.16 * _bell(_hz(m), 3.5), 0.75)
    _place(out, 0.4 * _kick(0.8), 0.75)
    return _reverb(out, 2.5, 0.45, seed=3)[: len(out)]


def write_wav(path, samples, peak: float = 0.89) -> float:
    """Normalize ``samples`` to ``peak`` and write a 16-bit stereo WAV; return its length."""
    require_numpy()
    path = Path(path)
    x = np.asarray(samples, dtype=float)
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    loudest = np.max(np.abs(x)) if x.size else 0.0
    if loudest > 0:
        x = x / loudest * peak
    data = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(_SR)
        w.writeframes(data.tobytes())
    return len(x) / _SR


def compose(out_dir, length: float = 95.0, bpm: float = 84, seed: int = 7) -> Dict[str, Path]:
    """Write music_bed.wav, intro_sting.wav and outro_sting.wav into ``out_dir``.

    Returns the three paths by name ("music_bed", "intro_sting", "outro_sting").
    """
    require_numpy()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {name: out_dir / f"{name}.wav" for name in ("music_bed", "intro_sting", "outro_sting")}
    write_wav(paths["music_bed"], music_bed(length, bpm=bpm, seed=seed))
    write_wav(paths["intro_sting"], intro_sting(seed=seed))
    write_wav(paths["outro_sting"], outro_sting())
    return paths
