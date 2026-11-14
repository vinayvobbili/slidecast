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

    code = segno.make(url, error="m", micro=False)
    modules = code.symbol_size(scale=1, border=QUIET_ZONE)[0]
    scale = max(1, size // modules)
    svg = code.svg_inline(scale=scale, border=QUIET_ZONE, dark="#0e0d1f", light="#ffffff", omitsize=True)
    return svg.replace("<svg ", f'<svg width="{size}" height="{size}" shape-rendering="crispEdges" ', 1)


def qr_tile(url: str, label: str = "", *, size: int = 180, body_color: str = "#f4f3ff") -> str:
    """The QR tile and its label as HTML, fixed to the bottom-right of the card.

    ``size`` is the code's width in pixels, about 160 to 200; ``label`` sits under it in ``body_color``.
    """
    if not url.strip():
        raise ValueError("closing_qr must be a URL")
    caption = (f'<div style="margin-top:12px;font:600 20px/1.3 system-ui,sans-serif;color:{body_color};'
               f'text-align:center;max-width:{size + 40}px">{escape(label)}</div>') if label.strip() else ""
    return (f'<div class="qr" style="position:absolute;right:48px;bottom:40px;display:flex;flex-direction:column;'
            f'align-items:center"><div style="background:#fff;border-radius:18px;padding:12px;line-height:0;'
            f'box-shadow:0 0 40px rgb(176 92 246 / .45)">{qr_svg(url, size)}</div>{caption}</div>')
