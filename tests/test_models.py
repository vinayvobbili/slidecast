import pytest

from pathlib import Path

from slidecast import Clip, Music, Slide, Sting


def test_slide_defaults():
    s = Slide(html="<h1>hi</h1>")
    assert s.narration == ""
    assert s.tail_pad == 0.0
    assert s.min_duration == 0.0


def test_slide_rejects_empty_html():
    with pytest.raises(ValueError):
        Slide(html="   ")


def test_slide_rejects_negative_timing():
    with pytest.raises(ValueError):
        Slide(html="<h1>x</h1>", tail_pad=-1)
    with pytest.raises(ValueError):
        Slide(html="<h1>x</h1>", min_duration=-0.5)


def test_clip_takes_a_path_and_defaults_to_silent():
    c = Clip(video="demo.mp4")
    assert c.video == Path("demo.mp4")
    assert c.narration == "" and c.tail_pad == 0.0 and c.min_duration == 0.0


def test_clip_rejects_missing_video_and_negative_timing():
    with pytest.raises(ValueError):
        Clip(video=" ")
    with pytest.raises(ValueError):
        Clip(video="a.mp4", tail_pad=-1)


def test_music_defaults_to_a_low_ducked_bed():
    m = Music("bed.mp3")
    assert m.file == Path("bed.mp3")
    assert (m.volume, m.fade_in, m.fade_out, m.duck) == (0.22, 1.0, 2.0, True)


def test_music_rejects_bad_values():
    with pytest.raises(ValueError):
        Music("  ")
    with pytest.raises(ValueError):
        Music("bed.mp3", volume=-0.1)
    with pytest.raises(ValueError):
        Music("bed.mp3", fade_out=-1)


def test_music_and_sting_coerce_paths():
    m = Music("bed.mp3", volume=0.1)
    assert Music.of(m) is m and Music.of(None) is None
    assert Music.of("bed.mp3") == Music("bed.mp3")
    assert Sting.of(Path("in.wav")) == Sting("in.wav")
    assert Sting("in.wav").volume is None  # role default: 0.75 intro, 0.7 outro
    with pytest.raises(ValueError):
        Sting("in.wav", volume=-1)
