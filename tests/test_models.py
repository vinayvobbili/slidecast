import pytest

from slidecast import Slide


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
