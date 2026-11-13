"""Render a short audio preview of a composed bed under a narrated line, mixed the way a reel mixes it.

    python scripts/music_preview.py out.m4a                      # the upbeat style, 20 s
    python scripts/music_preview.py out.m4a --style calm --say "Your own line."

The narration is spoken by macOS ``say``. The bed ducks under it by the same curve a reel uses, the intro sting
plays first, and the result is normalized to -16 LUFS.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from slidecast import Music, Sting, find_ffmpeg, probe_duration, sound
from slidecast.video import _audio_graph

LINE = ("Tired of typing the same answers into every job application? "
        "Meet your job-hunt helper. Let's see it fill one in.")


def preview(out: Path, *, style: str = "upbeat", say: str = LINE, length: float = 20.0, lead_in: float = 1.2) -> Path:
    ffmpeg = find_ffmpeg()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        subprocess.run(["say", "-o", str(work / "voice.aiff"), say], check=True)
        spoken = probe_duration(work / "voice.aiff", ffmpeg=ffmpeg)
        sound.write_wav(work / "bed.wav", sound.music_bed(length + 4, style=style))
        sound.write_wav(work / "intro.wav", sound.intro_sting(style=style))
        music = Music(work / "bed.wav", volume=0.3, fade_in=1.0, fade_out=3.0, duck=True, style=style)
        inputs, graph = _audio_graph(
            f"adelay={int(lead_in * 1000)}:all=1,apad=whole_dur={length}", length,
            music=music, intro=Sting(work / "intro.wav", volume=0.5), outro=None, outro_duration=0.0,
            loudness=-16, speech=[(lead_in, lead_in + spoken)], intro_duration=lead_in)
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", str(work / "voice.aiff"), *inputs,
               "-filter_complex", graph, "-map", "[a]", "-t", str(length), "-c:a", "aac", "-b:a", "192k", str(out)]
        subprocess.run(cmd, check=True)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--style", choices=sound.STYLES, default="upbeat")
    parser.add_argument("--say", default=LINE, help="the narrated line")
    parser.add_argument("--length", type=float, default=20.0)
    args = parser.parse_args(argv)
    print(f"Wrote {preview(args.out, style=args.style, say=args.say, length=args.length)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
