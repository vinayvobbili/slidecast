"""Story shape: title card first, problem before solution, a tagline, the closing reprise, accent colour, end checks."""

import subprocess

import pytest

from slidecast import Reel, ReelCheckError, SilentTTS, Theme
from slidecast import check as chk
from slidecast import cli
from slidecast import reel as reel_mod
from slidecast.ffmpeg import FFmpegNotFound, find_ffmpeg
from tests.fakes import FakeAnimator, FakeRunner

TITLE = "<!doctype html><html><body><h1>Initech Filer</h1></body></html>"
TAG = "Your paperwork shouldn't be a full-time job."


def build(tmp_path, monkeypatch, reel, **kw):
    monkeypatch.setattr(subprocess, "run", FakeRunner(touch_output=True))
    import slidecast.video as video
    monkeypatch.setattr(video, "probe_duration", lambda media, ffmpeg=None: 1.0)
    return reel.render(tmp_path / "out.mp4", ffmpeg="ff", cache=False, **kw)


def story(**kw):
    renderer = FakeAnimator()
    reel = Reel(width=640, height=400, fps=10, tts=SilentTTS(seconds=1.0), renderer=renderer, **kw)
    return reel, renderer


def test_title_card_is_first_and_silent_for_its_seconds():
    reel, _ = story()
    card = reel.title_card(TITLE)
    assert reel.slides == [card] and card.narration == "" and card.min_duration == 3.0
    with pytest.raises(ValueError):
        reel.title_card(TITLE)  # not first any more
    with pytest.raises(ValueError):
        story()[0].title_card(TITLE, seconds=0)


def test_tagline_is_spoken_and_shown_big_in_the_accent_gradient():
    reel, renderer = story()
    reel.title_card(TITLE)
    slide = reel.tagline(TAG)
    assert slide.narration == TAG and slide.html.count(TAG) == 1
    assert Theme().accent_gradient in slide.html and "background-clip: text" in slide.html
    with pytest.raises(ValueError):
        reel.tagline("  ")


def test_accent_is_a_gradient_never_pure_white():
    theme = Theme()
    assert "gradient" in theme.accent_gradient
    assert "#fff" not in theme.accent_gradient.lower() and "white" not in theme.accent_gradient
    assert "color: transparent" in theme.accent_css()
    custom = Theme(accent_gradient="linear-gradient(90deg, #0af, #f60)")
    assert "#0af" in custom.accent("Globex") and "Globex" in custom.accent("Globex")


def test_problem_runs_animated_before_the_payoff(tmp_path, monkeypatch):
    reel, renderer = story()
    reel.title_card(TITLE)
    reel.problem("<!doctype html><p>Forty forms</p>", "Forty forms, all alike.", seconds=2.0)
    reel.tagline(TAG)
    reel.add("<!doctype html><h1>Filled</h1>", "Umbrella's form, filled.")
    build(tmp_path, monkeypatch, reel, verify=False)
    # Title card, the animated problem, the tagline, the payoff, then the closing reprise.
    assert [bool(c.get("animate")) for c in renderer.calls] == [False, True, False, False, False]
    assert renderer.calls[1]["frames"] == 20  # 2 s at 10 fps


def test_the_reel_closes_on_the_title_card_with_the_tagline(tmp_path, monkeypatch):
    reel, renderer = story()
    reel.title_card(TITLE)
    reel.tagline(TAG)
    reel.add("<!doctype html><h1>Payoff</h1>", "Globex done.")
    build(tmp_path, monkeypatch, reel, verify=False)
    shots = [c for c in renderer.calls if not c.get("animate")]
    last = shots[-1]["html"]
    assert "Initech Filer" in last and TAG in last and "gradient" in last
    assert last.index("Initech Filer") < last.index(TAG)  # the tagline sits under the title
    assert len(reel.slides) == 3  # the reel's own list is untouched; the closing card is added at render


def test_close_on_title_can_be_turned_off(tmp_path, monkeypatch):
    reel, renderer = story(close_on_title=False)
    reel.title_card(TITLE)
    reel.add("<!doctype html><h1>Payoff</h1>", "Globex done.")
    build(tmp_path, monkeypatch, reel, verify=False)
    assert "Payoff" in renderer.calls[-1]["html"]


def test_a_reel_without_a_title_card_is_unchanged(tmp_path, monkeypatch):
    reel, renderer = story()
    reel.add("<!doctype html><h1>Only</h1>", "Hello.")
    seen = []
    monkeypatch.setattr(reel_mod._check, "check_ends", lambda *a, **k: seen.append(1) or [])
    build(tmp_path, monkeypatch, reel)
    assert len(renderer.calls) == 1 and not seen


def test_render_runs_the_end_checks_and_fails_on_a_problem(tmp_path, monkeypatch):
    reel, _ = story()
    reel.title_card(TITLE)
    monkeypatch.setattr(reel_mod._check, "check_ends", lambda *a, **k: ["the last 0.5 s has black frames"])
    with pytest.raises(ReelCheckError, match="black frames"):
        build(tmp_path, monkeypatch, reel)
    monkeypatch.setattr(reel_mod._check, "check_ends", lambda *a, **k: [])
    build(tmp_path, monkeypatch, reel)


def test_first_card_over_four_seconds_before_narration_is_flagged(monkeypatch):
    monkeypatch.setattr(chk, "black_tail", lambda *a, **k: False)
    monkeypatch.setattr(chk, "speech_levels", lambda *a, **k: [-70.0] * 100 + [-25.0] * 10)  # voice at 5.0 s
    (problem,) = chk.check_ends("out.mp4")
    assert "5.0 s before any narration" in problem
    monkeypatch.setattr(chk, "speech_levels", lambda *a, **k: [-70.0] * 60 + [-25.0] * 10)  # voice at 3.0 s
    assert chk.check_ends("out.mp4") == []
    monkeypatch.setattr(chk, "speech_levels", lambda *a, **k: [-70.0] * 100)  # no narration at all
    assert chk.check_ends("out.mp4") == []


def test_cli_check_fails_on_end_problems(monkeypatch, capsys):
    monkeypatch.setattr(chk, "check", lambda video: [])
    monkeypatch.setattr(chk, "check_ends", lambda video: ["the last 0.5 s has black frames"])
    assert cli.main(["check", "out.mp4"]) == 1
    assert "✗ the last 0.5 s has black frames" in capsys.readouterr().out


try:
    FFMPEG = find_ffmpeg()
except FFmpegNotFound:
    FFMPEG = None
real = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available")


def _video(tmp_path, name, colors):
    """A 25 fps clip of color segments [(color, seconds), ...], with a silent audio track."""
    parts = [["-f", "lavfi", "-i", f"color=c={c}:s=160x90:r=25:d={d}"] for c, d in colors]
    cmd = [FFMPEG, "-y", "-loglevel", "error", *sum(parts, [])]
    cmd += ["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono"]  # reels always carry an audio track
    cmd += ["-filter_complex", "".join(f"[{i}:v]" for i in range(len(colors))) + f"concat=n={len(colors)}:v=1[v]",
            "-map", "[v]", "-map", f"{len(colors)}:a", "-shortest", "-pix_fmt", "yuv420p", str(tmp_path / name)]
    subprocess.run(cmd, check=True)
    return tmp_path / name


@real
def test_blackdetect_catches_a_black_end_but_not_a_dark_brand_card(tmp_path):
    black_end = _video(tmp_path, "black.mp4", [("#6d5cff", 2), ("black", 1)])
    brand_end = _video(tmp_path, "brand.mp4", [("#6d5cff", 1), ("#0e0d1f", 2)])
    assert chk.black_tail(black_end, ffmpeg=FFMPEG)
    assert not chk.black_tail(brand_end, ffmpeg=FFMPEG)
    assert any("black frames" in p for p in chk.check_ends(black_end, ffmpeg=FFMPEG))
    assert chk.check_ends(brand_end, ffmpeg=FFMPEG) == []
    assert cli.main(["check", str(black_end)]) == 1
    assert cli.main(["check", str(brand_end)]) == 0


@real
def test_a_late_voice_is_flagged_in_a_real_file(tmp_path):
    silent = _video(tmp_path, "open.mp4", [("#6d5cff", 8)])
    out = tmp_path / "voiced.mp4"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(silent), "-f", "lavfi", "-i",
                    "sine=f=2500:d=2:sample_rate=24000", "-map", "0:v", "-map", "1:a", "-af", "adelay=5500|5500,apad", "-shortest",
                    "-c:v", "copy", str(out)], check=True)
    start = chk.first_speech(out, ffmpeg=FFMPEG)
    assert start == pytest.approx(5.5, abs=0.3)
    assert any("before any narration" in p for p in chk.check_ends(out, ffmpeg=FFMPEG))
