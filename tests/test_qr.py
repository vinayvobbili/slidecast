"""Reel(closing_qr=...): a QR code of a URL on the closing card, on a white tile that phones can scan."""

import pytest

from slidecast import PlaywrightRenderer, Reel, SilentTTS
from slidecast.qr import qr_tile
from tests.fakes import FakeRenderer

URL = "https://example.com/initech"
CARD = "<!doctype html><html><body style='margin:0;background:#0e0d1f'><h1>Initech</h1></body></html>"


def closing(**kwargs) -> str:
    reel = Reel(renderer=FakeRenderer(), tts=SilentTTS(seconds=1.0), **kwargs)
    reel.title_card(CARD)
    return reel._closing_slide().html


def test_the_closing_card_carries_the_code_and_its_label():
    html = closing(closing_qr=URL, closing_qr_label="Get it on the Initech store")
    assert "<svg" in html and "Get it on the Initech store" in html
    assert "position:absolute" not in html and "white-space:nowrap" in html  # in the flow, label on one line
    assert html.index("<h1>Initech</h1>") < html.index('class="qr"')  # laid over the card, not replacing it


def test_without_a_url_the_closing_card_has_no_code():
    assert "<svg" not in closing()


def test_a_blank_url_is_refused():
    with pytest.raises(ValueError):
        qr_tile("  ")


def _chromium_works():
    try:
        with PlaywrightRenderer(wait_ms=0):
            return True
    except Exception:
        return False


@pytest.mark.skipif(not _chromium_works(), reason="needs Chromium")
def test_a_scanner_reads_the_rendered_tile_back_as_the_url(tmp_path):
    cv2 = pytest.importorskip("cv2")
    page = f"<!doctype html><body style='margin:0;background:#0e0d1f'>{qr_tile(URL, 'Scan me')}</body>"
    shot = tmp_path / "card.png"
    with PlaywrightRenderer(wait_ms=0) as r:
        r.screenshot(page, shot, width=1280, height=800)
    text, _points, _raw = cv2.QRCodeDetector().detectAndDecode(cv2.imread(str(shot)))
    assert text == URL


@pytest.mark.skipif(not _chromium_works(), reason="needs Chromium")
def test_the_tile_sits_centred_under_a_centred_stack_with_a_one_line_label_and_balanced_margins():
    from playwright.sync_api import sync_playwright

    stack = ("<h1 style='font-size:62px;margin:0'>Initech</h1><p style='font-size:27px;margin:20px 0 0'>Fills forms"
             "</p><p style='font-size:40px;margin:30px 0 0'>Your paperwork shouldn't be a full-time job.</p>")
    page = ("<!doctype html><body style='margin:0;width:1280px;height:800px;display:flex;flex-direction:column;"
            f"align-items:center;justify-content:center;text-align:center'>{stack}"
            f"{qr_tile(URL, 'Get it on the Chrome Web Store')}</body>")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        tab = browser.new_page(viewport={"width": 1280, "height": 800})
        tab.set_content(page)
        box = tab.evaluate("""() => {
            const r = e => e.getBoundingClientRect();
            const qr = r(document.querySelector('.qr'));
            const label = r(document.querySelector('.qr > div:last-child'));
            return {top: r(document.querySelector('h1')).top, bottom: qr.bottom, x: qr.left + qr.width / 2,
                    labelHeight: label.height, labelWidth: label.width, tileWidth: r(document.querySelector('.qr svg')).width};
        }""")
        browser.close()
    assert abs(box["x"] - 640) < 2  # centred
    assert box["labelHeight"] < 40  # one line
    assert 190 <= box["tileWidth"] <= 210  # about 200 px
    assert abs(box["top"] - (800 - box["bottom"])) < 4 and box["top"] > 40  # balanced, and not jammed at an edge
