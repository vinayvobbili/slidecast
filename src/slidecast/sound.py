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


# ---------------------------------------------------------------------------------------------------------------
# The "upbeat" style: a 124 BPM dance bed. Drums and bass live below ~400 Hz and above ~5 kHz, and the finished mix
# is carved with a broad dip over the 1-4 kHz speech band, so a narrator stays clear on top of it.
# ---------------------------------------------------------------------------------------------------------------

STYLES = ("calm", "upbeat")
# Per style: default tempo, the length of one loop of its phrase in beats, and how many such phrases the looped
# long bed synthesizes. The upbeat phrase is 8 bars, so its builds line up wherever the loop joins.
_STYLE_INFO = {
    "calm": {"bpm": 84.0, "cycle_beats": 16, "cycles": 8},
    "upbeat": {"bpm": 124.0, "cycle_beats": 32, "cycles": 6},
}
SPEECH_BAND = (1000.0, 4000.0)
_CARVE_DEPTH = 0.8  # how much of the 1-4 kHz band the carve removes at its centre (0.8 -> -14 dB)


def check_style(style: str) -> str:
    if style not in STYLES:
        raise ValueError(f"unknown style {style!r}; choose from {', '.join(STYLES)}")
    return style


def default_bpm(style: str) -> float:
    """The tempo a style plays at unless one is asked for."""
    return _STYLE_INFO[check_style(style)]["bpm"]


def _highpass(x, cutoff):
    return x - _lowpass(x, cutoff)


def _carve_speech_band(x):
    """Dip the speech band (about 1-4 kHz) of a ``(samples, 2)`` mix, smoothly, by ``_CARVE_DEPTH``."""
    f = np.fft.rfftfreq(len(x), 1 / _SR)
    octaves = np.log2(np.maximum(f, 1.0) / 2000.0)
    gain = 1 - _CARVE_DEPTH * np.exp(-(octaves / 1.3) ** 2)
    return np.stack([np.fft.irfft(np.fft.rfft(x[:, c]) * gain, len(x)) for c in range(x.shape[1])], axis=1)


def _tone_stack(freq, dur, decay, bright=9000.0):
    """A bright additive pluck: a detuned pair of band-limited saw-ish voices."""
    t = _t(dur)
    s = np.zeros_like(t)
    for d in (-0.004, 0.004):
        f = freq * (1 + d)
        for k in range(1, 16):
            if f * k > bright:
                break
            s += np.sin(2 * np.pi * f * k * t) / k
    return s * np.exp(-t * decay) * _env(len(t), 0.003, 0.03)


def _clap(rng, dur=0.2):
    """A hand-clap: three quick noise bursts, all above ~4.5 kHz so it stays out of the speech band."""
    out = np.zeros(int(dur * _SR))
    for k, amp in enumerate((0.6, 0.8, 1.0)):
        burst = _highpass(rng.standard_normal(int(0.12 * _SR)), 4500) * np.exp(-_t(0.12) * 32)
        _place(out, burst * amp, k * 0.011)
    return out


def _hat(rng, open_=False):
    dur = 0.22 if open_ else 0.05
    return _highpass(rng.standard_normal(int(dur * _SR)), 7000) * np.exp(-_t(dur) * (16 if open_ else 80))


def _crash(rng, dur=1.6):
    n = int(dur * _SR)
    return _highpass(rng.standard_normal(n), 5000) * np.exp(-_t(dur) * 2.6) * _env(n, 0.004, 0.2)


def _riser(rng, dur):
    return _highpass(rng.standard_normal(int(dur * _SR)), 3500) * (_t(dur) / dur) ** 2


def _bass_note(freq, dur):
    t = _t(dur)
    s = np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(2 * np.pi * 2 * freq * t)
    return _lowpass(s, 380) * np.exp(-t * 4.5) * _env(len(t), 0.004, 0.04)


# C - G - Am - F, a bright major loop: the stab voicings, the bass roots, and the lead's motif as
# (16th-note step, chord tone) pairs, played an octave above the stabs.
_UP_STABS = [[64, 67, 72], [62, 67, 71], [64, 69, 72], [65, 69, 72]]
_UP_ROOTS = [36, 43, 45, 41]
_UP_MOTIF = [(0, 0), (3, 1), (6, 2), (8, 1), (11, 0), (14, 1)]
_UP_ANSWER = [(0, 2), (3, 1), (6, 0), (8, 1), (10, 2), (12, 1)]  # the second bar of each pair, falling
_BUILD_EVERY = 8  # bars between builds


def _upbeat_bed(length, bpm, seed):
    rng = np.random.default_rng(seed)
    beat = 60 / bpm
    bar = 4 * beat
    step = beat / 4
    n = int((length + 3) * _SR)
    stabs, lead, bass, drums = (np.zeros(n) for _ in range(4))
    kick, clap = _kick(0.3), _clap(rng)
    hats = [_hat(rng), _hat(rng)]
    open_hat, crash = _hat(rng, open_=True), _crash(rng)
    for b in range(int(np.ceil(length / bar))):
        at, c = b * bar, b % 4
        build = b % _BUILD_EVERY == _BUILD_EVERY - 1  # the bar before a landing: fill and riser
        landing = b % _BUILD_EVERY == 0 and b > 0
        for s in (2, 6, 10, 14):  # chord stabs and bass on the off-beat eighths, against the kick
            for note in _UP_STABS[c]:
                _place(stabs, _tone_stack(_hz(note), 0.28, 9), at + s * step)
            _place(bass, _bass_note(_hz(_UP_ROOTS[c] + (12 if s == 14 else 0)), 0.3), at + s * step)
        if b >= 1:  # the lead and the drums come in after the first bar
            for s, tone in (_UP_MOTIF if b % 2 == 0 else _UP_ANSWER):
                _place(lead, _tone_stack(_hz(_UP_STABS[c][tone] + 12), 0.32, 6), at + s * step)
            for k in range(3 if build else 4):  # four on the floor; a build's last beat is the roll
                _place(drums, kick, at + k * beat)
            for k in (1,) if build else (1, 3):  # clap on 2 and 4
                _place(drums, clap * 0.9, at + k * beat)
            for s in range(12 if build else 16):
                _place(drums, hats[s % 2] * (0.55 if s % 4 == 2 else 0.3), at + s * step)
            for s in (2, 6, 10) if build else (2, 6, 10, 14):
                _place(drums, open_hat * 0.35, at + s * step)
        else:
            for s in range(0, 16, 2):
                _place(drums, hats[(s // 2) % 2] * 0.3, at + s * step)
        if build:  # a snare roll in 32nds over the last beat, and a riser under the bar
            for i in range(8):
                _place(drums, clap * (0.35 + 0.08 * i), at + (12 + i * 0.5) * step)
            _place(drums, 0.35 * _riser(rng, bar), at)
        if landing:
            _place(drums, 0.5 * crash, at)
    melodic = _reverb(0.2 * stabs + 0.22 * lead, 0.7, 0.2, seed=1)[:n]
    low = 0.55 * bass + 0.95 * drums
    mix = _carve_speech_band((melodic + np.stack([low, low], axis=1))[: int(length * _SR)])
    # a soft clip raises the average level without a hard ceiling
    mix = np.tanh(1.6 * mix / max(float(np.max(np.abs(mix))), 1e-9))
    return mix * _env(len(mix), 1.5, 3.0)[:, None]


def _upbeat_intro(seed):
    """A punchy opener, 5.1 s: a snare roll and riser into a bright C chord, kick and crash at 1.0 s."""
    rng = np.random.default_rng(seed)
    out = np.zeros(int(5.1 * _SR))
    clap = _clap(rng)
    _place(out, 0.5 * _riser(rng, 1.0), 0.0)
    hits = 16
    for k in range(hits):  # the roll speeds up as it climbs
        _place(out, clap * (0.15 + 0.5 * k / hits), 1.0 * (1 - (1 - k / hits) ** 1.6))
    for m in (60, 64, 67, 72, 76):
        _place(out, 0.2 * _tone_stack(_hz(m + 12), 1.2, 3.2), 1.0)
        _place(out, 0.12 * _bell(_hz(m + 12), 2.6), 1.0)
    _place(out, 0.8 * _kick(0.5), 1.0)
    _place(out, 0.4 * np.sin(2 * np.pi * _hz(36) * _t(1.2)) * np.exp(-_t(1.2) * 4), 1.0)
    _place(out, 0.45 * _crash(rng), 1.0)
    _place(out, 0.8 * clap, 1.5)
    stereo = 0.3 * _reverb(out, 1.2, 0.25, seed=2)[: len(out)] + 0.7 * np.stack([out, out], axis=1)
    return _carve_speech_band(stereo)


def _upbeat_outro():
    """A punchy closer, 5.0 s: a quick climbing C arpeggio into a big chord, kick and clap at 0.75 s."""
    rng = np.random.default_rng(7)
    out = np.zeros(int(5.0 * _SR))
    for k, m in enumerate((72, 76, 79, 84, 88)):
        _place(out, 0.22 * _tone_stack(_hz(m), 0.3, 8), k * 0.125)
    for m in (48, 60, 64, 67, 72, 79):
        _place(out, 0.16 * _tone_stack(_hz(m), 1.4, 3.0), 0.75)
        _place(out, 0.1 * _bell(_hz(m + 12), 3.0), 0.75)
    _place(out, 0.8 * _kick(0.5), 0.75)
    _place(out, 0.7 * _clap(rng), 0.75)
    _place(out, 0.4 * _crash(rng), 0.75)
    stereo = 0.3 * _reverb(out, 1.6, 0.3, seed=3)[: len(out)] + 0.7 * np.stack([out, out], axis=1)
    return _carve_speech_band(stereo)


def music_bed(length: float = 95.0, bpm: float = None, seed: int = 7, style: str = "calm"):
    """A bed ``length`` seconds long, as a ``(samples, 2)`` float array.

    ``style`` is "calm" (a lo-fi loop at 84 BPM) or "upbeat" (a 124 BPM dance bed with a build every 8 bars;
    see the module notes). ``bpm`` defaults to the style's tempo.

    It fades in over 1.5 s and out over its last 3 s, so when it loops under a
    reel, make it longer than the reel (by 3 s or more) or the fade dips.
    ``seed`` picks the noise (and, for calm, the pad's phases); the same seed gives
    the same audio.
    """
    require_numpy()
    bpm = default_bpm(style) if bpm is None else bpm
    if length <= 0 or bpm <= 0:
        raise ValueError("length and bpm must be positive")
    if style == "upbeat":
        return _upbeat_bed(length, bpm, seed)
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


def intro_sting(seed: int = 7, style: str = "calm"):
    """An opening cue, 5.1 s: a swoosh rising to a bell-chord hit at 1.0 s, then its ring-out.

    The "upbeat" style swaps the swoosh for a snare roll and lands with a kick, a crash and a bright stab.
    """
    require_numpy()
    if check_style(style) == "upbeat":
        return _upbeat_intro(seed)
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


def outro_sting(style: str = "calm"):
    """A closing cue, 5.0 s: G–E–C–G bells falling onto a wide Cmaj7 at 0.75 s.

    The "upbeat" style climbs a quick arpeggio into a big chord with a kick, clap and crash.
    """
    require_numpy()
    if check_style(style) == "upbeat":
        return _upbeat_outro()
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


# A bed longer than this is built from a short synthesized one, looped: music_bed() holds several float64
# arrays and an FFT as long as the bed, which for a drill of nearly an hour is over 10 GB.
LOOP_ABOVE = 150.0
_SEAM = 1.0  # seconds crossfaded where one piece joins the next


def write_long_bed(path, length: float, bpm: float = None, seed: int = 7, peak: float = 0.89,
                   style: str = "calm") -> float:
    """Write a bed at least ``length`` seconds long as a WAV; return its length.

    Up to :data:`LOOP_ABOVE` this is :func:`music_bed` through :func:`write_wav`. Beyond it, the chord cycles in
    the middle of a short bed repeat (crossfaded where they join) between its own fade-in and fade-out, and the
    WAV is written in pieces, so memory stays the same however long the bed.
    """
    if length <= LOOP_ABOVE:
        return write_wav(path, music_bed(length, bpm=bpm, seed=seed, style=style), peak=peak)
    require_numpy()
    info = _STYLE_INFO[check_style(style)]
    bpm = info["bpm"] if bpm is None else bpm
    cycle = int(round(info["cycle_beats"] * 60 / bpm * _SR))
    src = music_bed(info["cycles"] * cycle / _SR, bpm=bpm, seed=seed, style=style)
    head, body, tail = src[: 2 * cycle], src[2 * cycle: 5 * cycle], src[5 * cycle:]
    scale = peak / max(float(np.max(np.abs(src))), 1e-12)
    seam = int(_SEAM * _SR)
    up = np.linspace(0, 1, seam)[:, None]
    wanted = int(length * _SR)
    repeats = max(1, int(np.ceil((wanted - len(head) - len(tail)) / (len(body) - seam))))
    pieces = [head] + [body] * repeats + [tail]
    written = 0
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(_SR)

        def put(x):
            nonlocal written
            w.writeframes((np.clip(x * scale, -1, 1) * 32767).astype("<i2").tobytes())
            written += len(x)

        held = pieces[0]
        for nxt in pieces[1:]:
            put(held[:-seam])
            put(held[-seam:] * (1 - up) + nxt[:seam] * up)
            held = nxt[seam:]
        put(held)
    return written / _SR


def compose(out_dir, length: float = 95.0, bpm: float = None, seed: int = 7, style: str = "calm") -> Dict[str, Path]:
    """Write music_bed.wav, intro_sting.wav and outro_sting.wav into ``out_dir``.

    Returns the three paths by name ("music_bed", "intro_sting", "outro_sting").
    """
    require_numpy()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {name: out_dir / f"{name}.wav" for name in ("music_bed", "intro_sting", "outro_sting")}
    write_wav(paths["music_bed"], music_bed(length, bpm=bpm, seed=seed, style=style))
    write_wav(paths["intro_sting"], intro_sting(seed=seed, style=style))
    write_wav(paths["outro_sting"], outro_sting(style=style))
    return paths
