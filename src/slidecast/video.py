"""ffmpeg steps: still image or clip + audio -> segment, concat segments, lay music
under the result, grab a poster.

Every function takes an injectable ``runner`` (defaults to ``subprocess.run``) and
an optional ``ffmpeg`` path, so callers can swap in the bundled binary and tests
can assert the exact command line without invoking ffmpeg.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import List, Optional, Tuple

from .ffmpeg import find_ffmpeg
from .models import Music, Sting

# How the bed ducks under the voice: past -34 dB of narration (0.02 linear) it is
# compressed 8:1, so normal speech pulls it down by ~10-12 dB. A 400 ms release
# lets it swell back in pauses without pumping between words.
DUCK = "threshold=0.02:ratio=8:attack=20:release=400"
# Default sting levels, when a Sting doesn't set its own volume.
INTRO_VOLUME = 0.75
OUTRO_VOLUME = 0.7
# A safety limiter on the final mix. level=0 turns off alimiter's auto-level,
# which would otherwise gain the whole mix up to the limit.
LIMITER = "alimiter=limit=0.95:level=0"


def build_segment(
    image: Path,
    audio: Path,
    out: Path,
    *,
    width: int,
    height: int,
    fps: int = 25,
    duration: Optional[float] = None,
    delay: float = 0.0,
    audio_bitrate: str = "192k",
    ffmpeg: Optional[str] = None,
    runner=None,
) -> Path:
    """Render one still image + its audio into an H.264/AAC MP4 segment.

    If ``duration`` is given, the segment is exactly that long and the audio is
    padded with silence to fill it (so narration is never clipped). If it's None,
    the audio drives the length (``-shortest``). ``delay`` holds the image silent
    for that many seconds before the audio starts (``duration`` includes it).
    """
    ffmpeg = ffmpeg or find_ffmpeg()
    runner = runner or subprocess.run
    cmd: List[str] = [
        ffmpeg, "-y", "-loglevel", "error",
        "-loop", "1", "-i", str(image),
        "-i", str(audio),
    ]
    afilter = [_adelay(delay)] if delay > 0 else []
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
        afilter.append("apad")
    else:
        cmd += ["-shortest"]
    if afilter:
        cmd += ["-af", ",".join(afilter)]
    cmd += [
        "-r", str(fps),
        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
        "-vf", f"scale={width}:{height}",
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-movflags", "+faststart",
        str(out),
    ]
    runner(cmd, check=True)
    return out


def build_clip_segment(
    clip: Path,
    audio: Path,
    out: Path,
    *,
    width: int,
    height: int,
    duration: float,
    hold: float = 0.0,
    delay: float = 0.0,
    fps: int = 25,
    audio_bitrate: str = "192k",
    ffmpeg: Optional[str] = None,
    runner=None,
) -> Path:
    """Render a recorded clip + narration into a segment that concats with slides.

    The clip is scaled to fit ``width`` x ``height`` (letterboxed, never
    stretched), resampled to ``fps``, and its last frame is held for ``hold``
    seconds. ``delay`` holds its first frame, silent, for that long before it
    plays. The segment is exactly ``duration`` long, with the narration padded
    by silence to fill it; the clip's own audio is dropped. Encoder settings
    match :func:`build_segment`, so the two concat losslessly.
    """
    ffmpeg = ffmpeg or find_ffmpeg()
    runner = runner or subprocess.run
    vchain = [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
        f"fps={fps}",
    ]
    if delay > 0:
        vchain.append(f"tpad=start_mode=clone:start_duration={delay:.3f}")
    if hold > 0:
        vchain.append(f"tpad=stop_mode=clone:stop_duration={hold:.3f}")
    vchain.append("format=yuv420p")
    achain = ([_adelay(delay)] if delay > 0 else []) + ["apad"]
    cmd: List[str] = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", str(clip),
        "-i", str(audio),
        "-filter_complex", f"[0:v]{','.join(vchain)}[v];[1:a]{','.join(achain)}[a]",
        "-map", "[v]", "-map", "[a]",
        "-t", f"{duration:.3f}",
        "-r", str(fps),
        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-movflags", "+faststart",
        str(out),
    ]
    runner(cmd, check=True)
    return out


def concat(
    segments: List[Path],
    out: Path,
    *,
    ffmpeg: Optional[str] = None,
    runner=None,
) -> Path:
    """Concatenate pre-encoded segments losslessly via the concat demuxer."""
    if not segments:
        raise ValueError("concat() needs at least one segment")
    ffmpeg = ffmpeg or find_ffmpeg()
    runner = runner or subprocess.run
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for seg in segments:
            f.write(f"file '{Path(seg).resolve()}'\n")
        list_path = Path(f.name)
    try:
        runner(
            [
                ffmpeg, "-y", "-loglevel", "error",
                "-f", "concat", "-safe", "0",
                "-i", str(list_path),
                "-c", "copy", "-movflags", "+faststart",
                str(out),
            ],
            check=True,
        )
    finally:
        list_path.unlink(missing_ok=True)
    return out


def _ffprobe_for(ffmpeg: str) -> Optional[str]:
    """Best-effort path to an ffprobe that matches ``ffmpeg`` (or one on PATH)."""
    cand = re.sub(r"ffmpeg(\.exe)?$", lambda m: "ffprobe" + (m.group(1) or ""), ffmpeg)
    if cand != ffmpeg and (shutil.which(cand) or os.path.isfile(cand)):
        return cand
    return shutil.which("ffprobe")


def probe_duration(media: Path, *, ffmpeg: Optional[str] = None) -> float:
    """Return the duration of ``media`` in seconds.

    Prefers ``ffprobe`` (matched to the ffmpeg binary, else one on PATH); falls
    back to parsing ``ffmpeg -i`` stderr so a probe-less install still works.
    """
    ffmpeg = ffmpeg or find_ffmpeg()
    probe = _ffprobe_for(ffmpeg)
    if probe:
        res = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(media)],
            capture_output=True, text=True,
        )
        try:
            return float(res.stdout.strip())
        except (ValueError, AttributeError):
            pass
    res = subprocess.run([ffmpeg, "-i", str(media)], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr)
    if m:
        h, mn, s = m.groups()
        return int(h) * 3600 + int(mn) * 60 + float(s)
    raise RuntimeError(f"could not determine duration of {media}")


def _adelay(seconds: float) -> str:
    """An ``adelay`` that shifts every channel by ``seconds``."""
    return f"adelay={round(seconds * 1000)}:all=1"


def _amix(labels: List[str], out: str) -> str:
    # normalize=0: amix would otherwise scale each input by 1/n, quietly turning
    # the narration down every time something is mixed under it.
    return (f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:"
            f"dropout_transition=0:normalize=0{out}")


def _loudnorm(target: float) -> str:
    # Single-pass loudnorm works at 192 kHz internally; resample back for AAC.
    return f"loudnorm=I={target:g}:TP=-1.5:LRA=11,aresample=48000"


def _audio_graph(
    narration: str,
    total: float,
    *,
    music: Optional[Music],
    intro: Optional[Sting],
    outro: Optional[Sting],
    outro_duration: float,
    loudness: Optional[float] = None,
) -> Tuple[List[str], str]:
    """Build the audio half of the mix: narration + stings + bed -> ``[a]``.

    ``narration`` is the filter chain applied to ``[0:a]`` (padding it to
    ``total``). Returns the extra ``-i`` arguments, in input order from 1, and the
    filter graph. Every leg is upmixed to stereo first: Kokoro narration is mono,
    and amix otherwise collapses the whole mix to mono, losing the bed's stereo
    image. Each is also brought to 48 kHz, or the mix would run at the
    narration's rate (24 kHz for Kokoro) and dull the music. The narration stays
    at full level; only the bed is attenuated. The final mix runs through a
    safety limiter, then ``loudnorm`` if ``loudness`` (integrated LUFS) is set.
    """
    if not (music or intro or outro):
        tail = f",{_loudnorm(loudness)}" if loudness is not None else ""
        return [], f"[0:a]{narration}{tail}[a]"
    final = f",{LIMITER}" + (f",{_loudnorm(loudness)}" if loudness is not None else "") + "[a]"
    inputs: List[str] = []
    graph = [f"[0:a]{narration},aformat=sample_rates=48000:channel_layouts=stereo[narr]"]
    voices = ["[narr]"]
    if music is not None:
        inputs += ["-stream_loop", "-1", "-i", str(music.file)]
    for name, sting in (("intro", intro), ("outro", outro)):
        if sting is None:
            continue
        inputs += ["-i", str(sting.file)]
        default = INTRO_VOLUME if name == "intro" else OUTRO_VOLUME
        chain = [f"volume={default if sting.volume is None else sting.volume}"]
        if name == "outro" and total > outro_duration:
            # Shifted so the outro's last note lands on the reel's last frame.
            chain.append(_adelay(total - outro_duration))
        chain.append("aformat=sample_rates=48000:channel_layouts=stereo")
        graph.append(f"[{inputs.count('-i')}:a]{','.join(chain)}[{name}]")
        voices.append(f"[{name}]")

    if music is None:
        graph.append(_amix(voices, final))
        return inputs, ";".join(graph)

    # The voice track is narration plus stings; with ducking it is also the
    # sidechain key, so the bed dips under the stings as well as the speech.
    if music.duck:
        if len(voices) > 1:
            graph.append(_amix(voices, ",asplit=2[voice][key]"))
        else:
            graph.append("[narr]asplit=2[voice][key]")
        voice = "[voice]"
    elif len(voices) > 1:
        graph.append(_amix(voices, "[voice]"))
        voice = "[voice]"
    else:
        voice = "[narr]"

    # The looped bed is trimmed to the reel, attenuated, (ducked,) then faded,
    # so the fades shape the final level rather than feeding the compressor.
    bed = [f"atrim=0:{total:.3f}", "asetpts=PTS-STARTPTS",
           f"volume={music.volume}", "aformat=sample_rates=48000:channel_layouts=stereo"]
    fades: List[str] = []
    if music.fade_in > 0:
        fades.append(f"afade=t=in:st=0:d={music.fade_in:.3f}")
    if music.fade_out > 0:
        fades.append(f"afade=t=out:st={max(total - music.fade_out, 0.0):.3f}"
                     f":d={music.fade_out:.3f}")
    if music.duck:
        graph.append(f"[1:a]{','.join(bed)}[music]")
        graph.append(f"[music][key]{','.join([f'sidechaincompress={DUCK}', *fades])}[bed]")
    else:
        graph.append(f"[1:a]{','.join(bed + fades)}[bed]")
    graph.append(_amix([voice, "[bed]"], final))
    return inputs, ";".join(graph)


def _outro_length(outro: Optional[Sting], outro_duration: Optional[float], ffmpeg: str) -> float:
    if outro is None:
        return 0.0
    if outro_duration is None:
        return probe_duration(outro.file, ffmpeg=ffmpeg)
    return outro_duration


def master(
    video: Path,
    out: Path,
    *,
    music=None,
    intro=None,
    outro=None,
    fade_in: float = 0.8,
    fade_out: float = 1.2,
    end_hold: float = 2.5,
    music_volume: Optional[float] = None,
    music_fade: Optional[float] = None,
    total_duration: Optional[float] = None,
    outro_duration: Optional[float] = None,
    loudness: Optional[float] = None,
    audio_bitrate: str = "192k",
    ffmpeg: Optional[str] = None,
    runner=None,
) -> Path:
    """Finish a concatenated reel: fade in/out, hold on the last frame, score it.

    A single ffmpeg pass that turns the raw concat into a polished cut:

    * ``fade_in`` / ``fade_out`` — video fade durations in seconds, so the reel
      eases in and out instead of cutting hard.
    * ``end_hold`` — seconds to freeze the final frame (cloned) after narration
      ends, so the last slide doesn't vanish mid-thought. Narration audio is
      padded with silence across the hold.
    * ``music`` — an optional bed under the whole reel: a :class:`Music` or a
      path (Music defaults), mixed as :func:`mix_music` describes.
      ``music_volume`` / ``music_fade`` override its volume and both its fades.
    * ``intro`` / ``outro`` — optional :class:`Sting` (or path) cues; the outro
      ends on the last held frame.
    * ``loudness`` — an integrated-loudness target in LUFS (e.g. -16 for web)
      applied to the final audio with ``loudnorm``. Off when None.

    The total length becomes ``duration(video) + end_hold``. The video is
    re-encoded here (the fades need it); use :func:`mix_music` to score a reel
    with its video stream copied.
    """
    ffmpeg = ffmpeg or find_ffmpeg()
    runner = runner or subprocess.run
    music, intro, outro = Music.of(music), Sting.of(intro), Sting.of(outro)
    if music is not None and music_volume is not None:
        music = replace(music, volume=music_volume)
    if music is not None and music_fade is not None:
        music = replace(music, fade_in=music_fade, fade_out=music_fade)
    end_hold = max(end_hold, 0.0)
    if total_duration is None:
        total_duration = probe_duration(video, ffmpeg=ffmpeg)
    total = total_duration + end_hold

    vchain: List[str] = []
    if end_hold > 0:
        vchain.append(f"tpad=stop_mode=clone:stop_duration={end_hold:.3f}")
    if fade_in > 0:
        vchain.append(f"fade=t=in:st=0:d={fade_in:.3f}")
    if fade_out > 0:
        vchain.append(f"fade=t=out:st={max(total - fade_out, 0.0):.3f}:d={fade_out:.3f}")
    vchain.append("format=yuv420p")
    vfilter = ",".join(vchain)

    inputs, agraph = _audio_graph(
        f"apad=pad_dur={end_hold:.3f}", total, music=music, intro=intro, outro=outro,
        outro_duration=_outro_length(outro, outro_duration, ffmpeg), loudness=loudness,
    )
    cmd: List[str] = [
        ffmpeg, "-y", "-loglevel", "error", "-i", str(video), *inputs,
        "-filter_complex", f"[0:v]{vfilter}[v];{agraph}", "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-movflags", "+faststart",
        str(out),
    ]
    runner(cmd, check=True)
    return out


def mix_music(
    video: Path,
    out: Path,
    music=None,
    *,
    intro=None,
    outro=None,
    total_duration: Optional[float] = None,
    outro_duration: Optional[float] = None,
    loudness: Optional[float] = None,
    audio_bitrate: str = "192k",
    ffmpeg: Optional[str] = None,
    runner=None,
) -> Path:
    """Lay a music bed and/or intro/outro stings under a reel's narration.

    Only the audio is re-encoded; the video stream is copied untouched.

    * ``music`` — a :class:`Music` (or a path, for its defaults): looped to the
      reel's length and trimmed to it, set to ``volume``, faded in and out, and,
      with ``duck``, sidechain-compressed under the voice so it dips while
      someone speaks.
    * ``intro`` / ``outro`` — :class:`Sting` (or path) cues. The intro starts at
      t=0; the outro is delayed so it ends with the reel. Neither is ducked; a
      ducking bed dips under them too.
    * ``loudness`` — an integrated-loudness target in LUFS for the final audio.

    Mixing uses ``normalize=0``, so the narration keeps its level, and a
    limiter (``limit=0.95``) catches any peaks the sum adds. Pass
    ``total_duration`` / ``outro_duration`` to skip probing for them.
    """
    music, intro, outro = Music.of(music), Sting.of(intro), Sting.of(outro)
    if not (music or intro or outro or loudness is not None):
        raise ValueError("mix_music() needs music, an intro, an outro or a loudness target")
    ffmpeg = ffmpeg or find_ffmpeg()
    runner = runner or subprocess.run
    if total_duration is None:
        total_duration = probe_duration(video, ffmpeg=ffmpeg)
    inputs, graph = _audio_graph(
        f"apad=whole_dur={total_duration:.3f}", total_duration,
        music=music, intro=intro, outro=outro,
        outro_duration=_outro_length(outro, outro_duration, ffmpeg), loudness=loudness,
    )
    cmd: List[str] = [
        ffmpeg, "-y", "-loglevel", "error", "-i", str(video), *inputs,
        "-filter_complex", graph, "-map", "0:v", "-map", "[a]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-movflags", "+faststart",
        str(out),
    ]
    runner(cmd, check=True)
    return out


def poster(
    video: Path,
    out: Path,
    *,
    quality: int = 3,
    at: float = 0.0,
    ffmpeg: Optional[str] = None,
    runner=None,
) -> Path:
    """Write the frame at ``at`` seconds (default the first) as a JPEG poster image.

    Pass ``at`` past any fade-in, or the poster comes out dark.
    """
    ffmpeg = ffmpeg or find_ffmpeg()
    runner = runner or subprocess.run
    seek = ["-ss", f"{at:.3f}"] if at > 0 else []
    runner(
        [
            ffmpeg, "-y", "-loglevel", "error", *seek,
            "-i", str(video), "-frames:v", "1", "-q:v", str(quality), str(out),
        ],
        check=True,
    )
    return out
