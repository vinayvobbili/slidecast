"""Record a web page the way a person uses it: a visible cursor that glides to each target, a ripple on every click,
a path drawn on drags, and a check that nothing is cut off at the frame's edges.

    pip install "slidecast[playwright]"

    from playwright.sync_api import sync_playwright
    from slidecast.recorder import Recorder

    with sync_playwright() as p:
        browser = p.chromium.launch()
        with Recorder.open(browser, "https://example.com/app", "scene.mp4") as rec:
            rec.click("text=Settings")
            rec.scroll_to("text=Save settings")      # lands below any sticky header; the framing check passes
            rec.hold(1.0)

The cursor and the ripples are elements injected into the page (:data:`OVERLAY_JS`), driven by the real mouse events
the recorder sends, so they appear in the recording and nowhere else. The motion is paced here, frame by frame:
Playwright's own ``mouse.move(steps=...)`` is instant, and a recording would show the cursor jumping.

:func:`find_clipped` is the framing check. It reports a visible element that is partly cut off by the viewport's top,
bottom or side edge: always for ``position: sticky`` and ``fixed`` elements (a tab bar sliced in half is the classic
mistake after a scroll), and for tabs. ``interesting=STRICT`` adds buttons, inputs and headings left sliced at the top
by a scroll. Content that merely runs off the bottom of the page (the fold) is not flagged, and a long page's content
scrolls past the top as it must; what an action targets is scrolled fully into view by :meth:`Recorder.reveal`.
"""

from __future__ import annotations

import contextlib
import math
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg import find_ffmpeg

# Elements that must not be sliced by the frame's edge, besides every sticky or fixed one.
INTERESTING = "[role=tab]"  # besides everything sticky or fixed
# Opt in (`interesting=STRICT`) for a page that is framed on one screen, where nothing should run off the top.
STRICT = "button, a.btn, input, select, textarea, h1, h2, h3, [role=tab], [role=button], nav a, label.btn"

OVERLAY_JS = r"""
(() => {
  if (window.__slOverlay || window !== window.top) return;  // one overlay, on the top page
  window.__slOverlay = true;
  const css = `
    #sl-cursor { position: fixed; left: 0; top: 0; width: 28px; height: 28px; z-index: 2147483647;
      pointer-events: none; will-change: transform; transition: scale .12s;
      filter: drop-shadow(0 2px 3px rgba(0,0,0,.45)); }
    #sl-cursor.down { scale: .86; }
    .sl-ripple { position: fixed; z-index: 2147483646; pointer-events: none; width: 14px; height: 14px;
      margin: -7px 0 0 -7px;
      border-radius: 50%; border: 3px solid #7c5cff; background: rgba(124,92,255,.28);
      animation: sl-ripple .6s ease-out forwards; }
    @keyframes sl-ripple { to { transform: scale(5); opacity: 0; } }
    #sl-trail { position: fixed; inset: 0; width: 100vw; height: 100vh; z-index: 2147483645; pointer-events: none; }
    #sl-trail path { fill: none; stroke: #7c5cff; stroke-width: 4; stroke-linecap: round; stroke-linejoin: round;
      stroke-dasharray: 2 9; opacity: .9; }
    #sl-trail.fade { opacity: 0; transition: opacity .6s; }`;
  const style = document.createElement("style");
  style.textContent = css;
  const cursor = document.createElement("div");
  cursor.id = "sl-cursor";
  cursor.innerHTML = '<svg viewBox="0 0 28 28" width="28" height="28"><path d="M5 3 L5 22 L10 17.5 L13.5 25 L17 23.5 ' +
    'L13.6 16.2 L20.5 16 Z" fill="#fff" stroke="#14122b" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  const trail = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  trail.id = "sl-trail";
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  trail.appendChild(path);
  let points = [], dragging = false;
  const place = (x, y) => {
    cursor.style.transform = `translate(${x - 5}px, ${y - 3}px)`;
    window.__slAt = [x, y];
    try { sessionStorage.setItem("__sl_cursor", JSON.stringify([x, y])); } catch (e) {}
  };
  const mount = () => {
    const root = document.documentElement;
    root.appendChild(style); root.appendChild(trail); root.appendChild(cursor);
    let at = [-100, -100];
    try { at = JSON.parse(sessionStorage.getItem("__sl_cursor")) || at; } catch (e) {}
    place(at[0], at[1]);
  };
  if (document.documentElement) mount();
  else document.addEventListener("DOMContentLoaded", mount, { once: true });
  // Driven by the recorder, not by the page's mouse events: those never reach the top page while the pointer is over
  // an iframe (a docked side panel), and the cursor must keep moving there.
  window.__slClicks = 0;
  window.__slMove = (x, y) => {
    place(x, y);
    if (dragging) {
      points.push([x, y]);
      path.setAttribute("d", "M" + points.map(p => p.join(",")).join(" L"));
    }
  };
  window.__slDown = (x, y, drag) => {
    window.__slClicks++;
    cursor.classList.add("down");
    const ripple = document.createElement("div");
    ripple.className = "sl-ripple";
    ripple.style.left = x + "px"; ripple.style.top = y + "px";
    document.documentElement.appendChild(ripple);
    setTimeout(() => ripple.remove(), 700);
    if (drag) {
      dragging = true; points = [[x, y]];
      trail.classList.remove("fade"); path.setAttribute("d", "");
    }
  };
  window.__slUp = () => {
    cursor.classList.remove("down");
    if (dragging) { dragging = false; setTimeout(() => trail.classList.add("fade"), 500); }
  };
})();
"""

CLIPPED_JS = r"""
([interesting, margin]) => {
  const vw = innerWidth, vh = innerHeight, found = [];
  const label = el => (el.id ? "#" + el.id : el.tagName.toLowerCase() +
    (el.classList.length ? "." + [...el.classList].slice(0, 2).join(".") : ""));
  for (const el of document.querySelectorAll("body *")) {
    if (el.closest("#sl-cursor, #sl-trail, .sl-ripple")) continue;
    const cs = getComputedStyle(el), r = el.getBoundingClientRect();
    if (cs.display === "none" || cs.visibility === "hidden" || +cs.opacity === 0) continue;
    if (r.width < 2 || r.height < 2) continue;
    // Sticky only counts when it sticks vertically (a table's row header that sticks left scrolls with the page).
    const pinned = cs.position === "fixed" ||
      (cs.position === "sticky" && (cs.top !== "auto" || cs.bottom !== "auto"));
    if (!pinned && !el.matches(interesting)) continue;
    if (r.height > vh * 0.5 && pinned) continue;  // a tall sticky sidebar or drawer runs past the fold by design
    // Cut by the viewport: it crosses an edge but is not wholly outside it.
    const edges = [];
    if (r.top < -margin && r.bottom > margin) edges.push("top");
    if (r.bottom > vh + margin && r.top < vh - margin) edges.push("bottom");
    if (r.left < -margin && r.right > margin) edges.push("left");
    if (r.right > vw + margin && r.left < vw - margin) edges.push("right");
    // Content scrolling in from the bottom edge is the page's fold, not a cut: only what is pinned, or what a
    // scroll left sliced at the top, counts.
    if (!pinned) { const top = edges.includes("top"); edges.length = 0; if (top) edges.push("top"); }
    if (!edges.length) continue;
    // Hidden by an ancestor's own overflow (a scroller) is that ancestor's business, not the frame's.
    let clippedByAncestor = false;
    for (let a = el.parentElement; a && a !== document.documentElement; a = a.parentElement) {
      const o = getComputedStyle(a);
      if (o.position === "fixed") break;
      if (/(auto|scroll|hidden|clip)/.test(o.overflow + o.overflowY + o.overflowX) && a !== document.body) {
        const ar = a.getBoundingClientRect();
        if (ar.top >= -margin && ar.bottom <= vh + margin && ar.left >= -margin && ar.right <= vw + margin) {
          clippedByAncestor = true; break;
        }
      }
    }
    if (clippedByAncestor) continue;
    found.push({ element: label(el), edges, pinned, text: (el.innerText || el.value || "").trim().slice(0, 40),
      rect: [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)] });
  }
  return found;
}
"""

# How far below the sticky headers a scrolled-to element lands, and the bottom bars it must clear.
SCROLL_TARGET_JS = r"""
(el, margin) => {
  const vh = innerHeight;
  let top = 0, bottom = vh;
  for (const e of document.querySelectorAll("body *")) {
    if (e === el || e.contains(el) || el.contains(e) || e.closest("#sl-cursor, #sl-trail")) continue;
    const p = getComputedStyle(e).position;
    if (p !== "sticky" && p !== "fixed") continue;
    const r = e.getBoundingClientRect();
    if (r.height < 1 || r.height > vh / 3 || r.width < innerWidth / 3) continue;
    if (r.top < vh / 3 && r.bottom > 0) top = Math.max(top, r.bottom);
    else if (r.bottom > vh * 2 / 3 && r.top < vh) bottom = Math.min(bottom, r.top);
  }
  const r = el.getBoundingClientRect();
  const room = bottom - top - 2 * margin;
  let want = top + margin;                     // its top just below the sticky header
  if (r.height > room) want = top + margin;    // taller than the room: show its start
  const max = document.documentElement.scrollHeight - vh;
  return { y: Math.max(0, Math.min(max, scrollY + r.top - want)), top, bottom };
}
"""


class ClippedError(AssertionError):
    """A visible element is partly cut off by the frame's edge."""


@dataclass(frozen=True)
class Clipped:
    element: str
    edges: tuple[str, ...]
    pinned: bool
    text: str
    rect: tuple[int, int, int, int]

    def __str__(self) -> str:
        what = f"{self.element} ({self.text!r})" if self.text else self.element
        pinned = " (sticky or fixed)" if self.pinned else ""
        return f"{what} is cut off at the {' and '.join(self.edges)} edge{pinned}"


def find_clipped(page, *, interesting: str = INTERESTING, margin: int = 1) -> list[Clipped]:
    """Every visible element of the page the viewport's edge slices in two: see the module's docs for which count."""
    rows = page.evaluate(CLIPPED_JS, [interesting, margin])
    return [Clipped(r["element"], tuple(r["edges"]), r["pinned"], r["text"], tuple(r["rect"])) for r in rows]


def check_framing(page, **kwargs) -> None:
    """Raise :class:`ClippedError` naming each element the frame's edge cuts in two."""
    clipped = find_clipped(page, **kwargs)
    if clipped:
        raise ClippedError("; ".join(str(c) for c in clipped))


def ease(t: float) -> float:
    """Ease in and out (cubic), for t in 0..1."""
    return 4 * t * t * t if t < 0.5 else 1 - math.pow(-2 * t + 2, 3) / 2


def glide_seconds(distance: float, pace: float = 1.0) -> float:
    """How long the cursor takes for `distance` pixels: quick for short hops, never abrupt or sluggish."""
    return min(1.1, 0.35 + distance / 1800) * pace


class Recorder:
    """A page, driven like a person: see the module's docs. Build one with :meth:`open`, or wrap a page you made.

    Args:
        page: A Playwright page. The overlay is injected on it.
        pace: Multiplies the time every glide and scroll takes (2.0: half speed).
        check: Run the framing check after each action and raise :class:`ClippedError`.
        interesting: A CSS selector for what the check guards besides sticky and fixed elements (see STRICT).
        shots: A folder to save a screenshot into after each action (a dry run that skips the video).
    """

    FRAME = 1 / 60

    def __init__(self, page, *, pace: float = 1.0, check: bool = True, interesting: str = INTERESTING,
                 shots: Path | None = None):
        self.page = page
        self.pace = pace
        self.check = check
        self.interesting = interesting
        self.shots = Path(shots) if shots else None
        self._x, self._y = -100.0, -100.0
        self._created = time.monotonic()
        self._begun: float | None = None
        self._count = 0
        page.add_init_script(OVERLAY_JS)  # every page it loads from now on
        with contextlib.suppress(Exception):
            page.evaluate(OVERLAY_JS)  # and the one already open (a no-op where the overlay is there)
        if self.shots:
            self.shots.mkdir(parents=True, exist_ok=True)

    # -- opening and saving ------------------------------------------------------------------------------------

    @classmethod
    @contextlib.contextmanager
    def open(cls, browser, url: str, out: Path | str | None, *, width: int = 1536, height: int = 864,
             scale: float = 1.25, size: tuple[int, int] = (1920, 1080), color_scheme: str = "light",
             settle: float = 1.2, **kwargs) -> Iterator[Recorder]:
        """A recorded page: opens `url` in a fresh context, calls :meth:`begin` once it has settled, and on leaving
        writes the video to `out` (an MP4 trimmed to start at :meth:`begin`). `out` None records nothing.

        The page is `width` x `height` CSS px at `scale`, and the MP4 is `size`: 1536 x 864 at 1.25 fills 1920 x 1080.
        Playwright records the viewport at its CSS size (it never scales a recording up, so a bigger record size only
        adds a grey band), so the recording is made at `width` x `height` and scaled to `size` on saving; the two must
        have the same aspect ratio or the frame would not be filled exactly.
        """
        if abs(width * size[1] - height * size[0]) > max(width, height):
            raise ValueError(f"the {width}x{height} page and the {size[0]}x{size[1]} frame differ in aspect ratio")
        tmp = Path(tempfile.mkdtemp(prefix="slidecast-rec-"))
        own = hasattr(browser, "new_context")  # a Browser: its own context; else a context made by the caller
        if own:
            options = {"viewport": {"width": width, "height": height}, "device_scale_factor": scale,
                       "color_scheme": color_scheme}
            if out is not None:
                options.update(record_video_dir=str(tmp), record_video_size={"width": width, "height": height})
            context = browser.new_context(**options)
        else:  # e.g. a persistent context with an extension: it was launched with the video and scale options
            context = browser
        page = context.new_page()
        if not own:
            page.set_viewport_size({"width": width, "height": height})
        rec = cls(page, **kwargs)
        failed = True
        try:
            if url:
                page.goto(url)
                page.wait_for_load_state("networkidle")
            page.wait_for_timeout(int(settle * 1000))
            rec.begin()
            yield rec
            rec.settle()
            failed = False
        finally:
            if failed and rec.shots:  # what the page showed when the scene broke
                with contextlib.suppress(Exception):
                    rec.shot("failed")
            video = page.video if out is not None else None
            if own:
                context.close()
            else:
                page.close()
            try:
                if video is not None and not failed:
                    save_video(Path(video.path()), Path(out), start=rec.started, size=size)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

    def begin(self) -> None:
        """Mark the moment the recording should start from (the page has loaded): everything before is trimmed."""
        self._begun = time.monotonic()

    @property
    def started(self) -> float:
        """Seconds from the page's creation to :meth:`begin` (0 before it): where the saved video starts."""
        return 0.0 if self._begun is None else self._begun - self._created

    @property
    def elapsed(self) -> float:
        """Seconds recorded so far."""
        return time.monotonic() - (self._begun if self._begun is not None else self._created)

    # -- the person's actions ----------------------------------------------------------------------------------

    def _locator(self, target):
        return self.page.locator(target).first if isinstance(target, str) else target.first

    def hold(self, seconds: float) -> None:
        """Do nothing for `seconds`, the cursor resting where it is."""
        if seconds > 0:
            self.page.wait_for_timeout(int(seconds * 1000))

    def hold_until(self, seconds: float) -> None:
        """Rest until the recording is `seconds` long, so a scene is as long as the voice over it."""
        self.hold(seconds - self.elapsed)

    def _point(self, target, where: tuple[float, float] = (0.5, 0.5)) -> tuple[float, float]:
        loc = self._locator(target)
        loc.wait_for(state="visible", timeout=8000)
        box = loc.bounding_box()
        if box is None:
            raise LookupError(f"{target!r} has no box on the page")
        return box["x"] + box["width"] * where[0], box["y"] + box["height"] * where[1]

    def glide(self, x: float, y: float, *, seconds: float | None = None) -> None:
        """Move the mouse (and so the cursor) to a point along an eased straight line."""
        if self._x < 0:  # first move: start from a corner rather than flying in from nowhere
            self._x, self._y = x - min(240, x), y - min(120, y)
            self._move(self._x, self._y)
        dx, dy = x - self._x, y - self._y
        distance = math.hypot(dx, dy)
        total = seconds if seconds is not None else glide_seconds(distance, self.pace)
        steps = max(1, round(total / self.FRAME)) if distance >= 1 else 0
        x0, y0 = self._x, self._y
        for i in range(1, steps + 1):
            k = ease(i / steps)
            self._move(x0 + dx * k, y0 + dy * k)
            self.page.wait_for_timeout(int(self.FRAME * 1000))
        self._x, self._y = x, y

    def _move(self, x: float, y: float) -> None:
        self.page.mouse.move(x, y)
        self.page.evaluate("([x, y]) => window.__slMove && window.__slMove(x, y)", [x, y])

    def _press(self, drag: bool = False) -> None:
        self.page.evaluate("([x, y, d]) => window.__slDown && window.__slDown(x, y, d)", [self._x, self._y, drag])
        self.page.mouse.down()

    def _release(self) -> None:
        self.page.mouse.up()
        self.page.evaluate("() => window.__slUp && window.__slUp()")

    def point_at(self, target, where: tuple[float, float] = (0.5, 0.5)) -> None:
        """Glide to a target without clicking (to draw the eye to it)."""
        self.reveal(target)
        self.glide(*self._point(target, where))

    def click(self, target, *, after: float = 0.5, where: tuple[float, float] = (0.5, 0.5)) -> None:
        """Scroll to the target if needed, glide to it, and click it (the ripple shows)."""
        self.reveal(target)
        self.glide(*self._point(target, where))
        self.page.wait_for_timeout(180)
        self._press()
        self.page.wait_for_timeout(90)
        self._release()
        self.hold(after)
        self.settle()

    def type(self, target, text: str, *, delay: float = 0.045, after: float = 0.4) -> None:
        """Click a field, then type into it one key at a time."""
        self.click(target, after=0.2)
        self.page.keyboard.type(text, delay=int(delay * 1000))
        self.hold(after)
        self.settle()

    def press(self, key: str, *, after: float = 0.4) -> None:
        self.page.keyboard.press(key)
        self.hold(after)
        self.settle()

    def drag(self, source, target, *, after: float = 0.5) -> None:
        """Press on the source, carry the cursor to the target (a dotted path follows it), and let go."""
        self.reveal(source)
        self.glide(*self._point(source))
        self.page.wait_for_timeout(150)
        self._press(drag=True)
        self.page.wait_for_timeout(120)
        self.glide(*self._point(target), seconds=max(0.6, glide_seconds(300, self.pace)))
        self.page.wait_for_timeout(120)
        self._release()
        self.hold(after)
        self.settle()

    def reveal(self, target, margin: int = 24) -> None:
        """Scroll the target into view if it is off screen or under a sticky header or bar."""
        loc = self._locator(target)
        loc.wait_for(state="attached", timeout=8000)
        if loc.evaluate("el => el.ownerDocument.defaultView !== window.top"):
            return  # in a frame (a docked panel): it scrolls itself
        plan = loc.evaluate(SCROLL_TARGET_JS, margin)
        box = loc.bounding_box()
        hidden = (box is None or box["y"] < plan["top"] + margin or box["y"] + box["height"] > plan["bottom"] - margin)
        if hidden and abs(plan["y"] - self.page.evaluate("() => scrollY")) > 2:
            self.scroll(plan["y"])

    def scroll_to(self, target, *, margin: int = 24) -> None:
        """Scroll so the target's top sits just below the sticky headers (smoothly, on screen)."""
        loc = self._locator(target)
        loc.wait_for(state="attached", timeout=8000)
        self.scroll(loc.evaluate(SCROLL_TARGET_JS, margin)["y"])
        self.hold(0.3)
        self.settle()

    def scroll(self, y: float, *, seconds: float | None = None) -> None:
        """Scroll the page to `y` along an eased path."""
        here = self.page.evaluate("() => scrollY")
        total = seconds if seconds is not None else min(1.4, 0.4 + abs(y - here) / 2200) * self.pace
        steps = max(1, round(total / self.FRAME))
        for i in range(1, steps + 1):
            self.page.evaluate("(v) => window.scrollTo(0, v)", here + (y - here) * ease(i / steps))
            self.page.wait_for_timeout(int(self.FRAME * 1000))

    def wheel(self, dy: float, *, at: tuple[float, float] | None = None, seconds: float | None = None) -> None:
        """Turn the wheel over a point (a scrollable panel or frame), a little at a time, so it can be followed."""
        if at is not None:
            self.glide(*at)
        total = seconds if seconds is not None else min(1.6, 0.4 + abs(dy) / 1500) * self.pace
        steps = max(1, round(total / self.FRAME))
        last = 0.0
        for i in range(1, steps + 1):
            to = dy * ease(i / steps)
            self.page.mouse.wheel(0, to - last)
            last = to
            self.page.wait_for_timeout(int(self.FRAME * 1000))
        self.hold(0.3)
        self.settle()

    # -- checks and stills -------------------------------------------------------------------------------------

    def settle(self) -> None:
        """After an action: run the framing check, and save a still when this is a dry run."""
        if self.check:
            check_framing(self.page, interesting=self.interesting)
        if self.shots:
            self._count += 1
            self.page.screenshot(path=str(self.shots / f"{self._count:02d}.png"))

    def shot(self, name: str) -> Path:
        """A named still (into `shots`, or the current folder)."""
        path = (self.shots or Path()) / f"{name}.png"
        self.page.screenshot(path=str(path))
        return path


def save_video(src: Path, out: Path, *, start: float = 0.0, fps: int = 25, ffmpeg: str | None = None,
               size: tuple[int, int] | None = None) -> Path:
    """Re-encode a Playwright recording as an H.264 MP4 that starts `start` seconds in, scaled to `size` if given."""
    scale = f",scale={size[0]}:{size[1]}:flags=lanczos,setsar=1" if size else ""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg or find_ffmpeg(), "-v", "error", "-y", "-ss", f"{max(0.0, start):.3f}", "-i", str(src),
           "-vf", f"fps={fps}{scale}", "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", "-an", str(out)]
    subprocess.run(cmd, check=True, timeout=600)
    return out
