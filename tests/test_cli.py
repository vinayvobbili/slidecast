import json
import subprocess

from slidecast.cli import _build_reel, _build_tts, _load_spec, main
from slidecast.tts import GTTSTTS, KokoroTTS, SilentTTS
from tests.fakes import FakeRunner


def test_load_json_spec(tmp_path):
    p = tmp_path / "spec.json"
    p.write_text(json.dumps({"width": 800, "slides": []}))
    assert _load_spec(p)["width"] == 800


def test_build_tts_variants():
    assert isinstance(_build_tts({"provider": "silent"}), SilentTTS)
    assert isinstance(_build_tts({"provider": "kokoro", "voice": "af_x"}), KokoroTTS)
    assert isinstance(_build_tts({"provider": "gtts", "tld": "co.uk"}), GTTSTTS)


def test_build_reel_inline_and_file_html(tmp_path):
    (tmp_path / "intro.html").write_text("<h1>from file</h1>")
    spec = {
        "width": 1000, "height": 500, "fps": 30,
        "tts": {"provider": "silent"},
        "slides": [
            {"html_file": "intro.html", "narration": "one", "tail_pad": 0.4},
            {"html": "<h1>inline</h1>", "narration": "", "min_duration": 2},
        ],
    }
    reel = _build_reel(spec, tmp_path)
    assert reel.width == 1000 and reel.height == 500 and reel.fps == 30
    assert len(reel.slides) == 2
    assert reel.slides[0].html == "<h1>from file</h1>"
    assert reel.slides[0].tail_pad == 0.4
    assert reel.slides[1].min_duration == 2


def test_main_render_end_to_end(monkeypatch, tmp_path, capsys):
    # Fake ffmpeg (no binary) — touch_output so the final mp4 exists for stat().
    monkeypatch.setattr(subprocess, "run", FakeRunner(touch_output=True))

    import slidecast.reel as reel_mod
    from tests.fakes import FakeRenderer

    # Force the default renderer to our fake regardless of spec.
    orig_render = reel_mod.Reel.render

    def render_with_fake(self, *a, **k):
        self.renderer = FakeRenderer()
        k.setdefault("ffmpeg", "ff")
        return orig_render(self, *a, **k)

    monkeypatch.setattr(reel_mod.Reel, "render", render_with_fake)

    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "width": 640, "height": 360,
        "tts": {"provider": "silent"},
        "slides": [{"html": "<h1>hi</h1>", "narration": "hello"}],
    }))
    out = tmp_path / "out.mp4"
    rc = main(["render", str(spec), "-o", str(out)])
    assert rc == 0
    assert out.exists()
    assert "wrote" in capsys.readouterr().out
