# slidecast

Turn a list of HTML slides + narration into a narrated MP4.

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

## CLI

```
slidecast render reel.yaml -o out.mp4 --poster
```

```yaml
width: 1280
height: 720
fps: 25
tts:
  provider: kokoro        # kokoro | gtts | silent
  url: http://127.0.0.1:8021/v1/audio/speech
  voice: af_heart
  response_format: wav
slides:
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
- `GTTSTTS` — Google Translate TTS (`gtts`).
- `SilentTTS` — a silent track of a fixed length. No dependencies; the default,
  so a reel renders end to end with nothing configured.

Pass `phonetic={r"\bSOC\b": "sock"}` to rewrite how tricky tokens are spoken
without changing the on-screen text.

**Renderer** — a context manager exposing `screenshot(html, path, *, width, height)`:

- `PlaywrightRenderer` — headless Chromium, launched once per reel (default).
- `ChromeBinaryRenderer` — drive an existing Chrome/Chromium binary by path.

**ffmpeg steps** are exposed directly (`build_segment`, `concat`, `poster`) and
take an injectable `runner`, so you can compose your own pipeline or test command
construction without invoking ffmpeg.

## License

MIT
