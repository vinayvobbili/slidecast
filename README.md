# slidecast

Turn a list of HTML slides and recorded clips, plus narration, into a narrated MP4.

You bring the slide design — any HTML you like — and the words. slidecast
screenshots each slide with a headless browser, narrates it with a pluggable
text-to-speech provider, and stitches the frames into one MP4 with ffmpeg. It
has no opinion about how your slides look and no hard dependency on a specific
voice or browser: every piece is swappable.

## Install

```
pip install slidecast              # core (requests only)
pip install slidecast[playwright]  # default renderer (headless Chromium)
pip install slidecast[gtts]        # Google Translate TTS
pip install slidecast[ffmpeg]      # bundled ffmpeg binary (no system install)
pip install 'slidecast[mlx]'       # Kokoro in-process on Apple Silicon (no TTS server)
```

After installing the Playwright extra, fetch the browser once:

```
playwright install chromium
```

You also need ffmpeg — on `PATH`, via `$SLIDECAST_FFMPEG`, or the `[ffmpeg]`
extra's bundled binary.

## Library

```python
from slidecast import Reel, KokoroTTS

reel = Reel(width=1280, height=720, tts=KokoroTTS(voice="af_heart"))
reel.add("<!doctype html><h1>Hello</h1>", "Hello, and welcome.")
reel.add("<!doctype html><h1>Goodbye</h1>", "Thanks for watching.", tail_pad=0.8)
reel.render("out.mp4", make_poster=True)
```

A slide with empty narration becomes a silent hold (`min_duration` seconds). When
the TTS provider reports a clip duration, the segment is padded to fit the speech
exactly; when it can't (e.g. MP3), the audio drives the length.

### Recorded clips

Mix screen recordings in with the slides. A clip plays through; if its narration
runs longer, the last frame holds until the voice finishes. It's letterboxed to
the reel's size, never stretched, and its own audio is replaced by the narration.

```python
reel.add("<!doctype html><h1>The demo</h1>", "Here's how it works.")
reel.add_clip("scan.webm", "Click Scan, and every question gets a cited draft.")
reel.add_clip("fill.webm", "Then fill the form.", tail_pad=0.6)
```

Record the clips any way you like: Playwright's `record_video_dir`, a
screenshot sequence encoded with ffmpeg, or a screen recorder.

### Music and stings

Lay a music bed under the narration, and bookend the reel with short stings:

```python
from slidecast import Music, Reel, Sting

reel = Reel(tts=..., music=Music("bed.mp3", volume=0.22, duck=True),
            intro="intro.wav", outro=Sting("outro.wav", volume=0.7), loudness=-16)
```

The bed loops to the reel's length and fades in and out. `volume` is its level
in the pauses. While someone speaks or a sting plays it dips `duck_db` lower
(12 dB by default): the reel knows when each line starts and ends, so the bed
eases down just before the line and swells back in the pause after it, with no
pumping between words. The intro plays
from the start, and the first narration waits for it (`lead_in`, which defaults
to the intro's length). The outro is timed to end on the last frame. The
narration keeps its level. `loudness` (LUFS) normalizes the final audio, and is
off by default. Only the audio is re-encoded; the video stream is copied, unless
`render(fade_in=..., end_hold=...)` asks for a re-encoded master anyway.

## CLI

```
slidecast render reel.yaml -o out.mp4 --poster
```

```yaml
width: 1280
height: 720
fps: 25
tts:
  provider: kokoro        # kokoro | mlx | gtts | say | silent
  url: http://127.0.0.1:8021/v1/audio/speech
  voice: af_heart
  response_format: wav
music: bed.mp3            # or {file, volume: 0.22, fade_in: 1.0, fade_out: 2.0, duck: true, duck_db: 12}
intro: intro.wav          # or {file, volume: 0.75}; the first narration waits for it
outro: {file: outro.wav, volume: 0.7}   # ends with the reel
lead_in: 1.8              # optional: silence before the first narration (default: intro length)
loudness: -16             # optional: LUFS target for the final audio
slides:
  - video: demo.mp4       # a recorded clip, relative to the spec
    narration: "Watch it run."
  - html_file: intro.html
    narration: "Before any of this, here's why it matters."
    tail_pad: 0.8
  - html: "<!doctype html><h1>Step one</h1>"
    narration: ""          # silent slide
    min_duration: 3
```

## The swappable pieces

**Text-to-speech** — anything with `synthesize(text, path) -> seconds | None`:

- `KokoroTTS` — any OpenAI-compatible `/v1/audio/speech` endpoint (Kokoro,
  OpenAI, LocalAI, …). Defaults to WAV so the clip length is measurable.
- `MLXKokoroTTS` — Kokoro running in-process on Apple Silicon via `mlx-audio`,
  so no server is needed: `MLXKokoroTTS(voice="af_heart", speed=1.0, lang_code="a")`,
  or `tts: {provider: mlx, voice: af_heart}` in YAML. The model
  (`mlx-community/Kokoro-82M-bf16`) downloads on first use and loads once per
  reel. If phonemizing fails with an espeak path under `/Users/runner/...` (a
  path baked into the `espeakng-loader` wheel), run `brew install espeak-ng`.
  slidecast then points `ESPEAK_DATA_PATH` and `PHONEMIZER_ESPEAK_LIBRARY` at
  Homebrew's copy itself, unless you've set them.
- `GTTSTTS` — Google Translate TTS (`gtts`).
- `MacSayTTS` — macOS's built-in `say`: offline, no dependencies, WAV. Premium
  voices (e.g. "Ava (Premium)") are a free download under System Settings ›
  Accessibility › Spoken Content.
- `SilentTTS` — a silent track of a fixed length. No dependencies; the default,
  so a reel renders end to end with nothing configured.

Pass `phonetic={r"\bSOC\b": "sock"}` to rewrite how tricky tokens are spoken
without changing the on-screen text.

**Renderer** — a context manager exposing `screenshot(html, path, *, width, height)`:

- `PlaywrightRenderer` — headless Chromium, launched once per reel (default).
- `ChromeBinaryRenderer` — drive an existing Chrome/Chromium binary by path.

**Music** — `Music(file, volume, fade_in, fade_out, duck, duck_db)` for the bed, and
`Sting(file, volume)` for the intro and outro. A plain path works for either
and uses the defaults.

**ffmpeg steps** are exposed directly (`build_segment`, `build_clip_segment`,
`concat`, `master`, `mix_music`, `poster`) and
take an injectable `runner`, so you can compose your own pipeline or test command
construction without invoking ffmpeg.

## License

MIT
