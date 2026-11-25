import json
import os
import subprocess
import time

import pytest

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
    assert _build_tts({"provider": "say", "voice": "Daniel"}).voice == "Daniel"
    mlx = _build_tts({"provider": "mlx", "voice": "bf_emma", "speed": 1.1, "lang_code": "b"})
    assert (mlx.voice, mlx.speed, mlx.lang_code) == ("bf_emma", 1.1, "b")
    assert mlx._model is None  # nothing loaded until the first line is spoken


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


def test_build_reel_video_entries_become_clips(tmp_path):
    from slidecast import Clip

    spec = {"slides": [{"video": "demo.mp4", "narration": "watch", "tail_pad": 0.5}]}
    reel = _build_reel(spec, tmp_path)
    clip = reel.slides[0]
    assert isinstance(clip, Clip)
    assert clip.video == tmp_path / "demo.mp4" and clip.narration == "watch" and clip.tail_pad == 0.5


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


def test_music_as_a_path_is_relative_to_the_spec(tmp_path):
    from slidecast import Music

    spec = tmp_path / "reel.yaml"
    spec.write_text("music: audio/bed.mp3\nslides: []\n")
    reel = _build_reel(_load_spec(spec), tmp_path)
    assert reel.music == Music(tmp_path / "audio" / "bed.mp3")
    assert reel.intro is None and reel.outro is None
    assert reel.lead_in is None and reel.loudness is None


def test_music_stings_lead_in_and_loudness_as_mappings(tmp_path):
    from slidecast import Music, Sting

    spec = tmp_path / "reel.yaml"
    spec.write_text(
        "music:\n"
        "  file: bed.mp3\n"
        "  volume: 0.1\n"
        "  fade_in: 0.5\n"
        "  fade_out: 3\n"
        "  duck: false\n"
        "  duck_db: 15\n"
        "intro: sting.wav\n"
        "outro: {file: end.wav, volume: 0.6}\n"
        "lead_in: 1.8\n"
        "loudness: -16\n"
        "slides: []\n"
    )
    reel = _build_reel(_load_spec(spec), tmp_path)
    assert reel.music == Music(tmp_path / "bed.mp3", volume=0.1, fade_in=0.5,
                               fade_out=3.0, duck=False, duck_db=15.0)
    assert reel.intro == Sting(tmp_path / "sting.wav")
    assert reel.outro == Sting(tmp_path / "end.wav", volume=0.6)
    assert reel.lead_in == 1.8 and reel.loudness == -16.0


def test_music_mapping_errors_are_reported(tmp_path):
    import pytest

    with pytest.raises(SystemExit, match="mapping with a 'file'"):
        _build_reel({"music": {"volume": 0.1}}, tmp_path)
    with pytest.raises(SystemExit, match="unknown 'music' option"):
        _build_reel({"music": {"file": "bed.mp3", "volum": 0.1}}, tmp_path)
    with pytest.raises(SystemExit, match="unknown 'intro' option"):
        _build_reel({"intro": {"file": "in.wav", "duck": True}}, tmp_path)


def test_compose_is_kept_unresolved_for_the_reel(tmp_path):
    from slidecast import Music, Sting

    spec = tmp_path / "reel.yaml"
    spec.write_text("music: {file: compose, volume: 0.15}\nintro: compose\noutro: compose\nslides: []\n")
    reel = _build_reel(_load_spec(spec), tmp_path)
    assert reel.music == Music("compose", volume=0.15) and reel.music.composed
    assert reel.intro == Sting("compose") and reel.intro.composed
    assert reel.outro.composed
    assert not Music(tmp_path / "compose.wav").composed


def test_main_compose_passes_its_options(monkeypatch, tmp_path, capsys):
    from slidecast import sound

    calls = []

    def fake_compose(out_dir, length, bpm, seed, style):
        calls.append((out_dir, length, bpm, seed, style))
        out_dir.mkdir()
        paths = {n: out_dir / f"{n}.wav" for n in ("music_bed", "intro_sting", "outro_sting")}
        for p in paths.values():
            SilentTTS(seconds=0.5).synthesize("", p)
        return paths

    monkeypatch.setattr(sound, "compose", fake_compose)
    assert main(["compose", "-o", str(tmp_path / "a"), "--length", "30", "--bpm", "90", "--seed", "3"]) == 0
    assert main(["compose", "-o", str(tmp_path / "b")]) == 0
    assert main(["compose", "-o", str(tmp_path / "c"), "--style", "upbeat"]) == 0
    assert calls == [(tmp_path / "a", 30.0, 90.0, 3, "calm"), (tmp_path / "b", 95.0, None, 7, "calm"),
                     (tmp_path / "c", 95.0, None, 7, "upbeat")]
    assert "music_bed.wav (0.5 s)" in capsys.readouterr().out


def test_main_youtube_writes_the_upload_copy(tmp_path, monkeypatch, capsys):
    from slidecast import video

    seen = []

    def fake(src, out, *, scale):
        seen.append((src, out, scale))
        target = out or video.youtube_path(src)
        target.write_bytes(b"x")
        return target

    monkeypatch.setattr(video, "youtube_copy", fake)
    assert main(["youtube", str(tmp_path / "demo.mp4")]) == 0
    assert seen == [(tmp_path / "demo.mp4", None, 2)]
    assert "demo-youtube.mp4" in capsys.readouterr().out


def test_main_youtube_rejects_a_zero_scale(capsys):
    import pytest

    with pytest.raises(SystemExit):
        main(["youtube", "x.mp4", "--scale", "0"])
    assert "--scale" in capsys.readouterr().err


def test_main_compose_rejects_a_zero_length(capsys):
    import pytest

    with pytest.raises(SystemExit):
        main(["compose", "-o", "x", "--length", "0"])
    assert "must be positive" in capsys.readouterr().err


def test_main_compose_writes_real_wavs(tmp_path, capsys):
    import pytest

    pytest.importorskip("numpy")
    from slidecast.tts import wav_duration

    assert main(["compose", "-o", str(tmp_path), "--length", "2"]) == 0
    assert wav_duration(tmp_path / "music_bed.wav") == pytest.approx(2.0)
    assert (tmp_path / "intro_sting.wav").exists() and (tmp_path / "outro_sting.wav").exists()


def _seed_cache(root, names=("old", "mid", "new"), size=100):
    seg = root / "segment"
    seg.mkdir(parents=True)
    for n, name in enumerate(names):
        path = seg / f"{name}.mkv"
        path.write_bytes(b"x" * size)
        stamp = time.time() - 1000 + n * 100
        os.utime(path, (stamp, stamp))


def test_cache_status_lists_each_folder(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("SLIDECAST_CACHE_MAX", raising=False)
    _seed_cache(tmp_path / "cache")
    assert main(["cache", "--dir", str(tmp_path / "cache")]) == 0
    out = capsys.readouterr().out
    assert "audio" in out and "segment" in out and "3 entries" in out and "300 B" in out
    assert "cap 2.0 GB" in out
    assert len(list((tmp_path / "cache" / "segment").iterdir())) == 3  # status deletes nothing


def test_cache_prune_dry_run_then_for_real(tmp_path, capsys):
    root = tmp_path / "cache"
    _seed_cache(root)
    assert main(["cache", "--dir", str(root), "--prune", "--max-size", "150", "--dry-run"]) == 0
    assert "would delete 2 least recently used entries, 200 B" in capsys.readouterr().out
    assert len(list((root / "segment").iterdir())) == 3
    assert main(["cache", "--dir", str(root), "--prune", "--max-size", "150"]) == 0
    assert "deleted 2 least recently used entries" in capsys.readouterr().out
    assert [p.name for p in (root / "segment").iterdir()] == ["new.mkv"]


def test_cache_prune_cap_defaults_to_the_environment(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    _seed_cache(root)
    monkeypatch.setenv("SLIDECAST_CACHE_MAX", "250")
    assert main(["cache", "--dir", str(root), "--prune"]) == 0
    assert sorted(p.name for p in (root / "segment").iterdir()) == ["mid.mkv", "new.mkv"]


def test_cache_prune_rejects_a_bad_size(tmp_path, capsys):
    with pytest.raises(SystemExit) as stop:
        main(["cache", "--dir", str(tmp_path), "--prune", "--max-size", "lots"])
    assert stop.value.code == 2
    assert "--max-size='lots'" in capsys.readouterr().err


def test_cache_size_and_dry_run_need_prune(tmp_path, capsys):
    with pytest.raises(SystemExit):
        main(["cache", "--dir", str(tmp_path), "--dry-run"])
    assert "go with --prune" in capsys.readouterr().err


def test_cache_command_says_when_the_cache_is_off(capsys, monkeypatch):
    monkeypatch.setenv("SLIDECAST_CACHE", "off")
    assert main(["cache"]) == 0
    assert "turned off" in capsys.readouterr().out
