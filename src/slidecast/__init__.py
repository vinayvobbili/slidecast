"""slidecast — turn a list of HTML slides + narration into a narrated MP4.

You bring the slide design (any HTML you like) and the words; slidecast
screenshots each slide with a headless browser, narrates it with a pluggable
text-to-speech provider, and stitches the frames into one MP4 with ffmpeg.

Quick start
-----------
    from slidecast import Reel, KokoroTTS

    reel = Reel(width=1280, height=720, tts=KokoroTTS(voice="af_heart"))
    reel.add("<!doctype html><h1>Hello</h1>", "Hello, and welcome.")
    reel.add("<!doctype html><h1>Bye</h1>", "Thanks for watching.", tail_pad=0.8)
    reel.render("out.mp4", make_poster=True)

Pieces (all swappable)
----------------------
Model:
    Slide(html, narration="", tail_pad=0.0, min_duration=0.0)
    Clip(video, narration="", tail_pad=0.0, min_duration=0.0)
    Music(file, volume=0.22, fade_in=1.0, fade_out=2.0, duck=True)
    Sting(file, volume=None)   # intro 0.75 / outro 0.7 when None
    Reel(width, height, fps, tts=..., renderer=..., music=..., intro=..., outro=...,
         lead_in=None, loudness=None).add(...).add_clip(...).render(out)
Text-to-speech (``synthesize(text, path) -> seconds | None``):
    KokoroTTS  — any OpenAI-compatible /v1/audio/speech endpoint
    MLXKokoroTTS — Kokoro in-process on Apple Silicon (mlx-audio), no server
    GTTSTTS    — Google Translate TTS (mp3)
    MacSayTTS  — macOS `say`, offline (wav)
    SilentTTS  — silent track, no deps (default)
Renderers (HTML -> PNG, used as a context manager):
    PlaywrightRenderer    — headless Chromium (default)
    ChromeBinaryRenderer  — drive an existing Chrome binary by path
ffmpeg steps (injectable runner, for direct use/testing):
    build_segment(...) / build_clip_segment(...) / concat(...) / poster(...)
    master(...) / mix_music(...)   # fades + end-hold / bed, stings, loudness
    find_ffmpeg() -> path   (PATH, $SLIDECAST_FFMPEG, or imageio-ffmpeg)
"""

from .ffmpeg import FFmpegNotFound, find_ffmpeg
from .models import Clip, Music, Segment, Slide, Sting
from .reel import Reel
from .render import ChromeBinaryRenderer, PlaywrightRenderer, Renderer
from .tts import (
    GTTSTTS,
    KokoroTTS,
    MacSayTTS,
    MLXKokoroTTS,
    SilentTTS,
    TTSProvider,
    apply_phonetic,
    wav_duration,
)
from .video import (
    build_clip_segment,
    build_segment,
    concat,
    master,
    mix_music,
    poster,
    probe_duration,
)

__version__ = "0.3.0"

__all__ = [
    "Slide",
    "Clip",
    "Segment",
    "Music",
    "Sting",
    "Reel",
    "Renderer",
    "PlaywrightRenderer",
    "ChromeBinaryRenderer",
    "TTSProvider",
    "KokoroTTS",
    "GTTSTTS",
    "MacSayTTS",
    "MLXKokoroTTS",
    "SilentTTS",
    "apply_phonetic",
    "wav_duration",
    "build_segment",
    "build_clip_segment",
    "concat",
    "master",
    "mix_music",
    "poster",
    "probe_duration",
    "find_ffmpeg",
    "FFmpegNotFound",
    "__version__",
]
