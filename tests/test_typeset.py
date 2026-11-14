import subprocess

import pytest

from slidecast import PlaywrightRenderer, Reel, SilentTTS
from slidecast.typeset import BASE_CSS, block_sentences, polish, sentences_on_lines
from tests.fakes import FakeRenderer, FakeRunner

SUB = "A free, open-source Chrome extension. You review every field, and press Submit yourself."


def test_each_sentence_gets_its_own_block():
    out = block_sentences(SUB)
    assert out.count('<span style="display:block">') == 2
    assert "extension.</span>" in out and out.endswith("yourself.</span>")


def test_one_sentence_is_left_alone():
    assert block_sentences("Fills forms for you.") == "Fills forms for you."


def test_plain_paragraphs_are_split_and_marked_up_ones_are_not():
    html = f'<h1>Initech</h1><p class="sub">{SUB}</p><p>One <b>two</b>. Three.</p>'
    out = sentences_on_lines(html)
    assert out.count("display:block") == 2
    assert "<p>One <b>two</b>. Three.</p>" in out


def test_polish_adds_the_base_css_first_in_the_head_once():
    html = "<!doctype html><html><head><meta charset=utf-8><style>p{color:red}</style></head><body></body></html>"
    out = polish(html)
    assert out.index(BASE_CSS) < out.index("p{color:red}")
    assert "text-wrap:pretty" in out and "text-wrap:balance" in out
    assert polish(out) == out
    assert polish("<p>bare</p>").startswith(BASE_CSS)


def test_title_card_and_closing_card_put_sentences_on_lines():
    reel = Reel(tts=SilentTTS(seconds=1), renderer=FakeRenderer())
    card = reel.title_card(f"<html><body><p>{SUB}</p></body></html>")
    assert card.html.count("display:block") == 2
    reel.tagline("Forms done. You press Submit.")
    assert reel.slides[1].html.count("display:block") == 2
    assert reel._closing_slide().html.count("display:block") == 4


def test_the_renderer_gets_the_polished_html(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", FakeRunner())
    fake = FakeRenderer()
    reel = Reel(width=64, height=64, tts=SilentTTS(seconds=1), renderer=fake)
    reel.add("<html><head></head><body>hi</body></html>", "hello")
    reel.render(tmp_path / "o.mp4", ffmpeg="ff", cache=False)
    assert BASE_CSS in fake.calls[0]["html"]


def _chromium_works():
    try:
        with PlaywrightRenderer(wait_ms=0):
            return True
    except Exception:
        return False


needs_chromium = pytest.mark.skipif(not _chromium_works(), reason="needs Chromium")

NARROW = "<!doctype html><body style='margin:0;font:20px monospace'><p style='width:%dch;margin:0'>%s</p></body>"


@needs_chromium
def test_a_lone_last_word_is_found_and_a_full_last_line_is_not():
    with PlaywrightRenderer(wait_ms=0) as r:
        # 10 chars per line: "aaaaaaaaa bbbbbbbbb c" leaves "c" alone.
        assert r.lone_words(NARROW % (10, "aaaaaaaaa bbbbbbbbb c"), width=400, height=200) == ["aaaaaaaaa bbbbbbbbb c"]
        assert r.lone_words(NARROW % (10, "aaaaaaaaa bbbb cc"), width=400, height=200) == []
        assert r.lone_words(NARROW % (40, "fits on one line"), width=400, height=200) == []


@needs_chromium
def test_text_wrap_pretty_keeps_a_last_line_from_being_one_word(tmp_path):
    text = "Fills the application for you and you check every field before pressing submit yourself"
    with PlaywrightRenderer(wait_ms=0) as r:
        raw = NARROW % (30, text)
        assert r.lone_words(polish(raw), width=800, height=200) == []


@needs_chromium
def test_render_warns_about_a_lone_word(tmp_path):
    from tests.test_real_ffmpeg import FFMPEG

    if FFMPEG is None:
        pytest.skip("needs ffmpeg")
    # The slide opts out of the base CSS's pretty wrapping, so the last line really is one word.
    html = NARROW % (10, "aaaaaaaaa bbbbbbbbb c")
    html = html.replace("margin:0'>", "margin:0;text-wrap:wrap'>", 1)
    reel = Reel(width=400, height=200, fps=10, tts=SilentTTS(seconds=1), renderer=PlaywrightRenderer(wait_ms=0))
    reel.add(html, "hi")
    with pytest.warns(UserWarning, match="single word"):
        reel.render(tmp_path / "o.mp4", ffmpeg=FFMPEG, cache=False)
