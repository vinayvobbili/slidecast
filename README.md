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
pip install 'slidecast[compose]'   # synthesize a music bed and stings (numpy)
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

### Animated slides

Write a page with CSS animations and `add_animated` turns it into a clip. Every animation is paused and stepped
frame by frame (the Web Animations API), so the result is the same on every run. If the narration is longer than
`seconds`, the last frame holds.

```python
reel.add_animated("""<!doctype html><style>
  body { background: #0b1020; margin: 0 }
  .bar { height: 40px; width: 0; background: #7c5cff; animation: grow 3s ease-out forwards }
  @keyframes grow { to { width: 100% } }
</style><div class="bar"></div>""", "Watch the bar fill.", seconds=3)
```

Start animations at page load, with CSS (keyframes, `animation-delay`). Anything driven by script timers won't
move. It needs a renderer with an `animate` method; `PlaywrightRenderer` has one.

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

### Composing a bed and stings

No music to hand? slidecast can write its own, synthesized from scratch with
numpy, so there is nothing to license. The bed is a soft lo-fi loop (Fmaj7 –
G6 – Em7 – Am7 at 84 BPM: pad, electric-piano arpeggio, bass, a soft kick and
shaker). The intro is a rising swoosh that lands on a bell chord at 1.0 s; the
outro is a run of falling bells that resolves on Cmaj7.

```
slidecast compose -o audio/ --length 95 --bpm 84 --seed 7
```

That writes `music_bed.wav`, `intro_sting.wav` (5.1 s) and `outro_sting.wav`
(5.0 s), 16-bit stereo. The same seed gives the same audio. The bed fades out
over its last 3 s, so make it a few seconds longer than the reel, or the fade
dips each time it loops.

Or let the reel compose them as it renders. It writes them into its work
directory and makes the bed outlast the reel:

```python
reel = Reel(tts=..., music="compose", intro="compose", outro="compose", lead_in=2.0)
reel = Reel(tts=..., music=Music("compose", volume=0.18))   # a composed bed, your settings
reel = Reel(tts=..., music=Music("compose", style="upbeat"), intro="compose", outro="compose")
```

`style="upbeat"` (or `slidecast compose --style upbeat`) is a 124 BPM dance bed: four-on-the-floor kick,
claps on 2 and 4, 16th hi-hats, an off-beat bass, bright chord stabs and a short lead motif, with a build
and crash every 8 bars. Its stings are punchier to match. The 1-4 kHz speech band is carved out of the mix,
and the bed ducks 18 dB under the voice by default (12 for the calm style).

In Python, `music_bed(length, bpm, seed)`, `intro_sting(seed)` and
`outro_sting()` return the audio as numpy arrays, and `write_wav(path, audio)`
saves one. The intro's ring-out runs to 5.1 s; a `lead_in` of about 2 s lets
the voice start over its tail instead of waiting for silence.

### Story shape

A pattern, not a template: use the parts that fit. A reel that opens with `title_card` gets the rest by default.

1. **Title card, briefly.** `reel.title_card(html, seconds=3)` shows the opening card silent for about 3 s (only
   the intro sting plays), then the story starts. Set `lead_in=0` so nothing waits on the sting.
2. **Problem before solution.** `reel.problem(html, narration, seconds=...)` runs an animated scene (it is
   `add_animated`) that shows the pain before the payoff: the same form retyped forty times, a queue growing.
3. **Tagline at the turn.** `reel.tagline("Your paperwork shouldn't be a full-time job.")` is spoken and shown
   big where the problem turns into the solution. Then the walkthrough.
4. **End on the title card, never black.** The reel closes on a reprise of the title card with the tagline under
   it, held `closing_seconds` (4 s), with no fade to black. Opt out with `Reel(close_on_title=False)`.
   `Reel(closing_qr="https://example.com/app", closing_qr_label="Get it here")` adds a QR code of the link,
   bottom-right on a white tile with a label under it, so a viewer can scan it from the screen.
5. **Accent text is a gradient, never pure white.** `Reel(theme=Theme(accent_gradient=...))`, lavender to pink by
   default, paints the tagline; `theme.accent("text")` and `theme.accent_css()` do the same in your own slides.
   Pure white is for body text.
6. **A check at the end of the build.** `render` runs `check_ends` on a reel with a title card and raises
   `ReelCheckError` if the last 0.5 s has black frames or the first card runs over 4 s before any narration
   (`verify=False` skips it, `verify=True` forces it). The same check is `slidecast check out.mp4`.

```python
from slidecast import Reel, Theme

reel = Reel(width=1280, height=720, tts=..., intro="compose", lead_in=0, theme=Theme())
reel.title_card(TITLE_HTML)                                                  # silent, 3 s
reel.problem(PROBLEM_HTML, "Forty forms, every one asking the same things.", seconds=12)
reel.tagline("Your paperwork shouldn't be a full-time job.")                 # the turn
reel.add(PAYOFF_HTML, "Initech's form, filled in seconds.")
reel.render("out.mp4")                                                       # closes on the title card, checked
```

## CLI

```
slidecast render reel.yaml -o out.mp4 --poster
slidecast check out.mp4 [--min-gap 0.6]   # exit 1: black in the last 0.5 s, a first card over 4 s before any voice,
                                          # or (with --min-gap) a slide change that cuts the voice off
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
music: bed.mp3            # or compose, or {file, volume: 0.22, fade_in: 1.0, fade_out: 2.0, duck: true, duck_db: 12}
intro: intro.wav          # or compose, or {file, volume: 0.75}; the first narration waits for it
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

## Rebuilding fast

Narration and built segments are cached, so after editing two slides of a long reel only those two are spoken
and encoded again. Entries are keyed on the text, voice and settings, and on the slide's screenshot, so a change
to anything that shows or sounds different is picked up.

The cache lives in `~/.cache/slidecast` (`$XDG_CACHE_HOME/slidecast` if set), or wherever `SLIDECAST_CACHE`
points, and is pruned to 5 GB, least recently used first. Set `SLIDECAST_CACHE_MAX` to change that cap: a byte
count, or a whole number with a `G` or `M` suffix, such as `SLIDECAST_CACHE_MAX=12G` or `SLIDECAST_CACHE_MAX=800M`.
A value it can't read stops the render with an error. Raise the cap when several long videos should keep their
clips, so editing one card doesn't re-render all of it. To turn it off, set `SLIDECAST_CACHE=off`, pass
`--no-cache` to `slidecast render`, or call `reel.render(out, cache=False)`. `--cache DIR` / `cache=DIR` use
another directory. A custom TTS provider is keyed on its public attributes; give it a `cache_key` if its output
depends on anything else.

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

Kokoro can't tell "read" (reed) from "read" (red), so slidecast picks from the words around it: red after
have/has/had/'ve/'d/was/were/been/is/are/be/being/get/got/already/just/never/well/widely/recently/once, or
with yesterday/ago/earlier/"last week" a few words after it (also re-read, proofread, misread); reed after
to/will/can/do/I/you/we/they and for an imperative like "Read them out loud". To force a reading, write
`{read:past}` or `{read:present}` in the narration (`{Read:past}`, `{re-read:present}`, `{proofread:past}` and
`{misread:past}` work too); the Kokoro engine turns it into the right sound and keeps the word.

**Renderer** — a context manager exposing `screenshot(html, path, *, width, height)`:

- `PlaywrightRenderer` — headless Chromium, launched once per reel (default).
- `ChromeBinaryRenderer` — drive an existing Chrome/Chromium binary by path.

**Music** — `Music(file, volume, fade_in, fade_out, duck, duck_db)` for the bed, and
`Sting(file, volume)` for the intro and outro. A plain path works for either
and uses the defaults; `"compose"` synthesizes one.

**ffmpeg steps** are exposed directly (`build_segment`, `build_clip_segment`,
`concat`, `master`, `mix_music`, `poster`) and
take an injectable `runner`, so you can compose your own pipeline or test command
construction without invoking ffmpeg.

## Releasing

A `v*` tag publishes to PyPI. `scripts/release.py 0.4.1 -m "what's in it"` bumps the version in
`pyproject.toml` and `slidecast/__init__.py`, runs the tests, commits, tags, pushes, and waits until PyPI
has it (`--dry-run` shows the bump first).

## License

MIT
