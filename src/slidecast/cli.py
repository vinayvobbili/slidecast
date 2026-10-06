"""Command line: render a reel from a spec file, or compose music for one.

    slidecast render reel.yaml -o out.mp4 --poster
    slidecast compose -o audio/ --length 95 --bpm 84 --seed 7

A spec is YAML or JSON::

    width: 1280
    height: 720
    fps: 25
    tts:
      provider: kokoro        # kokoro | mlx | gtts | say | silent
      url: http://127.0.0.1:8021/v1/audio/speech
      voice: af_heart
      response_format: wav
    music: bed.mp3            # a path, "compose", or a mapping:
    # music: {file: bed.mp3, volume: 0.22, fade_in: 1.0, fade_out: 2.0, duck: true, duck_db: 12}
    intro: sting.wav          # or "compose", or {file: sting.wav, volume: 0.75}
    outro: {file: outro.wav, volume: 0.7}
    lead_in: 1.8              # silence before the first narration (default: intro length)
    loudness: -16             # LUFS target for the final audio (default: off)
    slides:
      - html_file: intro.html       # path (relative to the spec) ...
        narration: "Welcome."
        tail_pad: 0.8
      - html: "<!doctype html>..."  # ... or inline HTML
        narration: ""               # empty => silent slide
      - video: demo.mp4             # a recorded clip; holds its last frame
        narration: "Here it is."    # until the narration ends
        min_duration: 3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

from . import sound
from .models import COMPOSE, Music, Sting
from .reel import Reel
from .render import ChromeBinaryRenderer
from .tts import GTTSTTS, KokoroTTS, MacSayTTS, MLXKokoroTTS, SilentTTS, wav_duration


def _load_spec(path: Path) -> Dict[str, Any]:
    text = path.read_text()
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:  # noqa: BLE001
            raise SystemExit("YAML spec needs PyYAML — `pip install slidecast[yaml]`")
        return yaml.safe_load(text)
    return json.loads(text)


def _build_tts(cfg: Dict[str, Any]):
    cfg = dict(cfg or {})
    provider = (cfg.pop("provider", "silent") or "silent").lower()
    if provider == "kokoro":
        return KokoroTTS(**cfg)
    if provider == "mlx":
        return MLXKokoroTTS(**cfg)
    if provider == "gtts":
        return GTTSTTS(**cfg)
    if provider == "say":
        return MacSayTTS(**cfg)
    if provider == "silent":
        return SilentTTS(**cfg) if cfg else SilentTTS()
    raise SystemExit(f"Unknown tts provider: {provider!r}")


def _audio_cfg(cfg: Any, base: Path, key: str, allowed: Dict[str, type]) -> Dict[str, Any]:
    """Normalize a ``key:`` entry that is a path or a mapping with a ``file``.

    A ``file`` of "compose" stays as is, for the reel to synthesize.
    """
    def resolve(file):
        return file if file == COMPOSE else base / file

    if isinstance(cfg, (str, Path)):
        return {"file": resolve(cfg)}
    if not isinstance(cfg, dict) or not cfg.get("file"):
        raise SystemExit(f"'{key}' must be a path or a mapping with a 'file'")
    unknown = set(cfg) - set(allowed) - {"file"}
    if unknown:
        raise SystemExit(f"unknown '{key}' option(s): {', '.join(sorted(unknown))}")
    kw = {k: cast(cfg[k]) for k, cast in allowed.items() if k in cfg}
    return {"file": resolve(cfg["file"]), **kw}


def _build_music(cfg: Any, base: Path):
    if not cfg:
        return None
    return Music(**_audio_cfg(cfg, base, "music", {
        "volume": float, "fade_in": float, "fade_out": float, "duck": bool, "duck_db": float}))


def _build_sting(cfg: Any, base: Path, key: str):
    if not cfg:
        return None
    return Sting(**_audio_cfg(cfg, base, key, {"volume": float}))


def _build_reel(spec: Dict[str, Any], base: Path) -> Reel:
    reel = Reel(
        width=int(spec.get("width", 1920)),
        height=int(spec.get("height", 1080)),
        fps=int(spec.get("fps", 25)),
        tts=_build_tts(spec.get("tts", {})),
        silent_slide_seconds=float(spec.get("silent_slide_seconds", 3.0)),
        music=_build_music(spec.get("music"), base),
        intro=_build_sting(spec.get("intro"), base, "intro"),
        outro=_build_sting(spec.get("outro"), base, "outro"),
        lead_in=None if spec.get("lead_in") is None else float(spec["lead_in"]),
        loudness=None if spec.get("loudness") is None else float(spec["loudness"]),
    )
    chrome = spec.get("chrome")
    if chrome:
        reel.renderer = ChromeBinaryRenderer(chrome=chrome)
    for s in spec.get("slides", []):
        if s.get("video"):
            reel.add_clip(base / s["video"], s.get("narration", ""),
                          tail_pad=float(s.get("tail_pad", 0.0)),
                          min_duration=float(s.get("min_duration", 0.0)))
            continue
        html = s.get("html")
        if not html and s.get("html_file"):
            html = (base / s["html_file"]).read_text()
        if not html:
            raise SystemExit("each slide needs 'html', 'html_file' or 'video'")
        reel.add(html, s.get("narration", ""),
                 tail_pad=float(s.get("tail_pad", 0.0)),
                 min_duration=float(s.get("min_duration", 0.0)))
    return reel


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="slidecast", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="render a reel spec to an MP4")
    r.add_argument("spec", type=Path, help="YAML or JSON reel spec")
    r.add_argument("-o", "--out", type=Path, required=True, help="output .mp4 path")
    r.add_argument("--poster", action="store_true", help="also write <stem>_poster.jpg")
    r.add_argument("--keep-work", type=Path, default=None,
                   help="keep intermediate frames/audio in this directory")
    c = sub.add_parser("compose", help="synthesize a music bed and intro/outro stings")
    c.add_argument("-o", "--out", type=Path, required=True, metavar="DIR",
                   help="directory for music_bed.wav, intro_sting.wav, outro_sting.wav")
    c.add_argument("--length", type=float, default=95.0, metavar="SECONDS",
                   help="bed length in seconds; make it outlast the reel (default 95)")
    c.add_argument("--bpm", type=float, default=84, metavar="N", help="bed tempo (default 84)")
    c.add_argument("--seed", type=int, default=7, metavar="N",
                   help="same seed, same audio (default 7)")
    args = parser.parse_args(argv)

    if args.cmd == "compose":
        if args.length <= 0 or args.bpm <= 0:
            parser.error("--length and --bpm must be positive")
        try:
            paths = sound.compose(args.out, length=args.length, bpm=args.bpm, seed=args.seed)
        except ImportError as e:
            raise SystemExit(str(e))
        for path in paths.values():
            print(f"✓ wrote {path} ({wav_duration(path):.1f} s)")

    if args.cmd == "render":
        spec = _load_spec(args.spec)
        reel = _build_reel(spec, args.spec.resolve().parent)
        n = len(reel.slides)

        def progress(i, total, slide):
            head = slide.narration[:48].replace("\n", " ") or "(silent)"
            print(f"  [{i}/{total}] {head}", file=sys.stderr)

        out = reel.render(args.out, make_poster=args.poster,
                          workdir=args.keep_work, on_progress=progress)
        size_mb = out.stat().st_size / 1e6
        print(f"✓ wrote {out} ({size_mb:.1f} MB) from {n} slides")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
