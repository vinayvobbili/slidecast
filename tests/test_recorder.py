"""slidecast.recorder: the cursor overlay, click ripples, drag paths, scrolling under sticky headers, and the check
that fails when the frame's edge slices a visible element in two."""

import subprocess
from urllib.parse import quote

import pytest

from slidecast.ffmpeg import FFmpegNotFound, find_ffmpeg
from slidecast.recorder import (
    STRICT,
    ClippedError,
    Recorder,
    check_framing,
    ease,
    find_clipped,
    glide_seconds,
)

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # the playwright extra isn't installed
    sync_playwright = None

try:
    FFMPEG = find_ffmpeg()
except FFmpegNotFound:
    FFMPEG = None

PAGE = """<!doctype html><style>
  body { margin: 0; font: 16px sans-serif; height: 3000px }
  header { position: sticky; top: 0; height: 60px; background: #335; color: #fff }
  #bar { position: fixed; bottom: 0; left: 0; right: 0; height: 50px; background: #eee }
  button { display: block; margin: 300px 40px 0; padding: 12px }
  #src, #dst { position: absolute; width: 80px; height: 40px; top: 200px }
  #src { left: 40px; background: #cde } #dst { left: 400px; background: #fdc }
</style>
<header>Tabs <button id="tab" style="margin:0;display:inline">Settings</button></header>
<div id="src">drag me</div><div id="dst">drop here</div>
<button id="a">Alpha</button><button id="b">Bravo</button><button id="save">Save settings</button>
<div id="bar"><button id="foot" style="margin:0">Footer</button></div>
<script>window.log = []; for (const t of ["mousedown", "mouseup"]) addEventListener(t, e => log.push(t));</script>"""
URL = "data:text/html," + quote(PAGE)


@pytest.fixture(scope="module")
def browser():
    if sync_playwright is None:
        pytest.skip("playwright isn't installed")
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:
            pytest.skip("Chromium isn't installed")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 800, "height": 500})
    page = context.new_page()
    yield page
    context.close()


def test_ease_runs_from_0_to_1_and_glides_scale_with_distance():
    assert ease(0) == 0 and ease(1) == 1 and 0.4 < ease(0.5) < 0.6
    assert glide_seconds(10) < glide_seconds(800) <= 1.1
    assert glide_seconds(800, pace=2) == pytest.approx(2 * glide_seconds(800))


def test_a_sticky_bar_sliced_by_the_top_edge_is_reported(page):
    page.goto(URL)
    assert find_clipped(page) == []
    page.evaluate("() => Object.assign(document.querySelector('header').style, {position: 'fixed', top: '-30px'})")
    clipped = find_clipped(page)
    assert any(c.element == "header" and c.edges == ("top",) for c in clipped)
    with pytest.raises(ClippedError, match=r"header.*top edge"):
        check_framing(page)


def test_a_fixed_bar_cut_by_the_bottom_edge_is_reported_but_a_whole_one_is_not(page):
    page.goto(URL)
    assert find_clipped(page) == []
    page.evaluate("() => document.getElementById('bar').style.bottom = '-20px'")
    assert [c.element for c in find_clipped(page) if c.pinned] == ["#bar"]


def test_a_button_sliced_at_the_top_is_reported_but_the_fold_at_the_bottom_is_not(page):
    page.goto(URL)
    page.evaluate("() => document.getElementById('save').scrollIntoView({block: 'end'})")
    page.evaluate("() => scrollBy(0, -20)")  # its bottom runs 20 px past the fold: the page goes on, so no cut
    assert find_clipped(page, interesting=STRICT) == []
    page.evaluate("() => document.getElementById('save').scrollIntoView({block: 'start'})")
    page.evaluate("() => scrollBy(0, 20)")  # now a scroll has sliced it at the top
    assert find_clipped(page) == []  # ordinary content scrolls past the top as it must
    ids = [c.element for c in find_clipped(page, interesting=STRICT)]
    assert "#save" in ids and "#src" not in ids  # a div is not "interesting" unless it is pinned


def test_scroll_to_lands_below_the_sticky_header_and_passes_the_check(page):
    page.goto(URL)
    rec = Recorder(page, pace=0.2)
    rec.scroll_to("#save")
    top = page.evaluate("() => document.getElementById('save').getBoundingClientRect().top")
    assert top >= 60  # below the 60 px sticky header, not under it
    assert find_clipped(page) == []


def test_click_glides_there_ripples_and_the_cursor_follows(page):
    page.goto(URL)
    rec = Recorder(page, pace=0.2)
    rec.click("#a", after=0)
    assert page.evaluate("() => window.__slClicks") == 1
    assert page.evaluate("() => log.slice(-2)") == ["mousedown", "mouseup"]
    box = page.locator("#a").bounding_box()
    x, y = page.evaluate("() => window.__slAt")
    assert box["x"] < x < box["x"] + box["width"] and box["y"] < y < box["y"] + box["height"]
    assert page.locator("#sl-cursor").count() == 1


def test_the_cursor_keeps_moving_and_ripples_over_an_iframe(page):
    page.goto("data:text/html," + quote(
        "<body style='margin:0'><iframe style='position:fixed;right:0;top:0;width:300px;height:400px;border:0' "
        "srcdoc=\"<button id=f onclick=document.title='hit'>Fill</button>\"></iframe>"))
    rec = Recorder(page, pace=0.2)
    inner = page.frame_locator("iframe").locator("#f")
    rec.click(inner, after=0)
    assert page.frame_locator("iframe").locator("html").evaluate("() => document.title") == "hit"
    assert page.evaluate("() => window.__slClicks") == 1  # the top page's overlay saw it, not the frame's
    box = inner.bounding_box()
    x, _ = page.evaluate("() => window.__slAt")
    assert box["x"] < x < box["x"] + box["width"]
    assert page.locator("#sl-cursor").count() == 1


def test_the_cursor_is_not_in_the_framing_check(page):
    page.goto(URL)
    rec = Recorder(page, pace=0.2)
    rec.glide(795, 495)  # half off the corner
    assert find_clipped(page) == []


def test_drag_draws_a_path_and_releases_on_the_target(page):
    page.goto(URL)
    rec = Recorder(page, pace=0.2)
    rec.drag("#src", "#dst", after=0)
    assert page.evaluate("() => log.slice(-2)") == ["mousedown", "mouseup"]
    path = page.locator("#sl-trail path").get_attribute("d")
    assert path.startswith("M") and path.count("L") > 5
    box = page.locator("#dst").bounding_box()
    x, _ = page.evaluate("() => window.__slAt")
    assert box["x"] < x < box["x"] + box["width"]


def test_an_action_that_leaves_a_pinned_element_cut_raises(page):
    page.goto(URL)
    page.evaluate("() => document.getElementById('bar').style.bottom = '-20px'")
    with pytest.raises(ClippedError):
        Recorder(page, pace=0.2).settle()
    Recorder(page, check=False).settle()  # opt out


def test_dry_run_saves_a_still_after_each_action(page, tmp_path):
    page.goto(URL)
    rec = Recorder(page, pace=0.2, shots=tmp_path / "shots")
    rec.click("#a", after=0)
    rec.click("#b", after=0)
    assert sorted(p.name for p in (tmp_path / "shots").glob("*.png")) == ["01.png", "02.png"]


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available")
def test_open_records_an_mp4_that_starts_after_the_page_loaded(browser, tmp_path):
    out = tmp_path / "scene.mp4"
    with Recorder.open(browser, URL, out, width=640, height=360, size=(640, 360), scale=1, settle=0.2,
                       pace=0.3) as rec:
        rec.click("#a", after=0.3)
        rec.hold_until(1.6)
    probe = subprocess.run([FFMPEG, "-i", str(out)], capture_output=True, text=True).stderr
    assert "Video: h264" in probe and "640x360" in probe
    assert "Duration: 00:00:0" in probe


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available")
def test_a_scaled_page_fills_the_whole_frame_with_no_grey_band(browser, tmp_path):
    """Playwright never scales a recording up: a 640x360 page recorded into a 960x540 frame left a band (the bug)."""
    out = tmp_path / "scene.mp4"
    with Recorder.open(browser, URL, out, width=640, height=360, size=(960, 540), scale=1.5, settle=0.2,
                       pace=0.3) as rec:
        rec.hold_until(1.0)
    probe = subprocess.run([FFMPEG, "-i", str(out)], capture_output=True, text=True).stderr
    assert "960x540" in probe
    still = tmp_path / "last.png"
    subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-y", "-sseof", "-0.3", "-i", str(out), "-frames:v", "1",
                    "-vf", "crop=40:40:900:480", str(still)], check=True)
    pix = subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-i", str(still), "-f", "rawvideo", "-pix_fmt", "gray",
                          "-"], capture_output=True).stdout
    assert min(pix) > 200  # the bottom-right corner is the white page, not the recorder's grey


def test_a_frame_of_another_aspect_ratio_is_refused(browser, tmp_path):
    with pytest.raises(ValueError, match="aspect ratio"):
        with Recorder.open(browser, URL, tmp_path / "x.mp4", width=1000, height=864, size=(1920, 1080)):
            pass
