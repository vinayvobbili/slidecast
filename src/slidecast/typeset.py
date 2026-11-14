"""Line breaks for slide text: no sentence split across lines when it fits, no paragraph ending on a lone word.

Three pieces, all applied by :class:`~slidecast.reel.Reel`:

* :func:`sentences_on_lines` puts each sentence of a plain-text ``<p>`` on its own line (title cards, taglines).
* :func:`polish` adds a base stylesheet, ``text-wrap: pretty`` for text and ``balance`` for headings, ahead of
  the slide's own CSS so a slide can still override it.
* :data:`LONE_WORD_JS` finds the text blocks whose last line is a single word, for the render-time warning.
"""

from __future__ import annotations

import re

BASE_CSS = ("<style data-slidecast=\"typeset\">"
            "p,li,dd,blockquote{text-wrap:pretty}h1,h2,h3,h4{text-wrap:balance}"
            "</style>")

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_PLAIN_P = re.compile(r"(<p\b[^>]*>)([^<]+)(</p>)", re.IGNORECASE)


def sentences(text: str) -> list:
    """``text`` split after each ., ! or ? that is followed by a space."""
    return [s for s in _SENTENCE_END.split(text.strip()) if s]


def block_sentences(text: str) -> str:
    """``text`` with each sentence in its own block span, so it starts a line. One sentence is left as it is."""
    parts = sentences(text)
    if len(parts) < 2:
        return text
    return "".join(f'<span style="display:block">{s}</span>' for s in parts)


def sentences_on_lines(html: str) -> str:
    """``html`` with the text of each ``<p>`` that holds only plain text put one sentence per line."""
    return _PLAIN_P.sub(lambda m: m.group(1) + block_sentences(m.group(2)) + m.group(3), html)


def polish(html: str) -> str:
    """``html`` with :data:`BASE_CSS` first in its ``<head>`` (or first in the document without one)."""
    if "data-slidecast=\"typeset\"" in html:
        return html
    m = re.search(r"<head\b[^>]*>", html, re.IGNORECASE)
    if m:
        return html[:m.end()] + BASE_CSS + html[m.end():]
    m = re.search(r"<html\b[^>]*>", html, re.IGNORECASE)
    if m:
        return html[:m.end()] + BASE_CSS + html[m.end():]
    return BASE_CSS + html


# Runs in the page: the text of every block that holds one text node and whose last word sits alone on its line.
LONE_WORD_JS = """() => {
  const found = [];
  for (const el of document.querySelectorAll('p,li,h1,h2,h3,h4,dd,blockquote,span')) {
    if (el.childNodes.length !== 1 || el.firstChild.nodeType !== 3) continue;
    const node = el.firstChild, text = node.textContent.replace(/\\s+$/, '');
    const space = text.lastIndexOf(' ');
    if (space < 1 || text.length - space < 2) continue;
    const at = (a, b) => { const r = document.createRange(); r.setStart(node, a); r.setEnd(node, b);
                           const rects = r.getClientRects();
                           return rects.length ? rects[rects.length - 1].top : null; };
    const last = at(space + 1, text.length), before = at(space - 1, space);
    if (last !== null && before !== null && last - before > 2) found.push(text.trim());
  }
  return found;
}"""
