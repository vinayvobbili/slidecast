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
    assert "right:24px" in html and "bottom:24px" in html  # bottom-right
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
