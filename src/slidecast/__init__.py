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
    Reel(width, height, fps, tts=..., renderer=...).add(...).render(out)
Text-to-speech (``synthesize(text, path) -> seconds | None``):
    KokoroTTS  — any OpenAI-compatible /v1/audio/speech endpoint
    GTTSTTS    — Google Translate TTS (mp3)
    SilentTTS  — silent track, no deps (default)
Renderers (HTML -> PNG, used as a context manager):
    PlaywrightRenderer    — headless Chromium (default)
    ChromeBinaryRenderer  — drive an existing Chrome binary by path
ffmpeg steps (injectable runner, for direct use/testing):
    build_segment(...) / concat(...) / poster(...)
    find_ffmpeg() -> path   (PATH, $SLIDECAST_FFMPEG, or imageio-ffmpeg)
"""

from .ffmpeg import FFmpegNotFound, find_ffmpeg
from .models import Slide
from .reel import Reel
from .render import ChromeBinaryRenderer, PlaywrightRenderer, Renderer
from .tts import (
    GTTSTTS,
    KokoroTTS,
    SilentTTS,
    TTSProvider,
    apply_phonetic,
    wav_duration,
)
from .video import build_segment, concat, master, poster, probe_duration

__version__ = "0.2.0"

__all__ = [
    "Slide",
    "Reel",
    "Renderer",
    "PlaywrightRenderer",
    "ChromeBinaryRenderer",
    "TTSProvider",
    "KokoroTTS",
    "GTTSTTS",
    "SilentTTS",
    "apply_phonetic",
    "wav_duration",
    "build_segment",
    "concat",
    "master",
    "poster",
    "probe_duration",
    "find_ffmpeg",
    "FFmpegNotFound",
    "__version__",
]
