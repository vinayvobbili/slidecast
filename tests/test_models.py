import pytest

from pathlib import Path

from slidecast import Clip, Slide


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
