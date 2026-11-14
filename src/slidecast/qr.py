"""A QR code as an inline SVG on a white tile, for a reel's closing card (``Reel(closing_qr=...)``).

The tile is white with a quiet zone around the code, so a phone scans it off a dark card. The code is made with
segno (pure Python) and embedded as SVG, so it stays crisp at any size.
"""

from __future__ import annotations

from html import escape

QUIET_ZONE = 4  # modules of white around the code: the QR spec's minimum, which scanners expect


def qr_svg(url: str, size: int) -> str:
    """An inline SVG of ``url`` as a QR code, ``size`` pixels square, quiet zone included."""
    import segno

    # Error level L keeps the code small (a long URL stays a few dozen modules wide), so each module can be several
    # pixels: a screen has no smudges to correct, and a phone reads big modules from across a room. Whole pixels per
    # module keep the edges sharp, so the code is the largest whole multiple of its modules that fits ``size``.
    code = segno.make(url, error="l", micro=False)
    modules = code.symbol_size(scale=1, border=QUIET_ZONE)[0]
    scale = max(1, size // modules)
    svg = code.svg_inline(scale=scale, border=QUIET_ZONE, dark="#0e0d1f", light="#ffffff", omitsize=True)
    side = modules * scale
    return svg.replace("<svg ", f'<svg width="{side}" height="{side}" shape-rendering="crispEdges" ', 1)


def qr_tile(url: str, label: str = "", *, size: int = 200, body_color: str = "#f4f3ff") -> str:
    """The QR tile and its label as HTML, centred in the card's flow under whatever comes before it.

    On a card whose content is centred (a title card), adding the tile moves the whole stack up to make room, so
    the card stays balanced top and bottom. ``size`` is the code's width in pixels, about 160 to 200; ``label`` sits
    under it on one line, in ``body_color``.
    """
    if not url.strip():
        raise ValueError("closing_qr must be a URL")
    caption = (f'<div style="margin-top:14px;font:600 22px/1.3 system-ui,sans-serif;color:{body_color};'
               f'text-align:center;white-space:nowrap">{escape(label)}</div>') if label.strip() else ""
    return (f'<div class="qr" style="position:relative;margin:30px auto 0;width:max-content;display:flex;'
            f'flex-direction:column;align-items:center"><div style="background:#fff;border-radius:18px;'
            f'padding:12px;line-height:0;box-shadow:0 0 40px rgb(176 92 246 / .45)">{qr_svg(url, size)}</div>'
            f'{caption}</div>')
