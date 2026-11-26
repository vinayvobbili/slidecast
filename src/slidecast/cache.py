"""A persistent cache of narration audio and built segments, so a rebuild only redoes what changed.

Speaking a line and encoding a segment are the slow steps of a render. Each result is stored under a hash of
everything that can change it, so an edit to two slides in a long reel redoes two slides and reuses the rest.
Entries are written to a temporary name and renamed into place, so an interrupted render never leaves a half-written
entry, and the least recently used ones are pruned, at the start and the end of every render, once the cache grows
past its cap: :data:`MAX_BYTES` (2 GB) unless
``$SLIDECAST_CACHE_MAX`` says otherwise, as a byte count or a number with a ``G`` or ``M`` suffix (``12G``, ``800M``).

The cache lives in ``$SLIDECAST_CACHE``, else ``$XDG_CACHE_HOME/slidecast``, else ``~/.cache/slidecast``. Set
``SLIDECAST_CACHE`` to ``off`` (or pass ``cache=False`` to :meth:`Reel.render`) to turn it off.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

# Bump to retire every entry when a change here or in video.py alters what a key doesn't see.
VERSION = 1

# Pruned down to this, least recently used first, at the start and the end of a render. ``$SLIDECAST_CACHE_MAX``
# overrides it.
MAX_BYTES = 2 * 1024 ** 3

# What the segment builders encode with. A change to video.py's settings goes here, so old segments aren't reused.
ENCODE = "libx264/stillimage/yuv420p;pcm_s16le/48000/2;aac/192k"



def encode_key(crf: int | None = None) -> str:
    """ENCODE, plus the constant-quality setting when a reel asks for one (the default key is unchanged)."""
    return ENCODE if crf is None else f"{ENCODE};crf{crf}/slow"


KINDS = ("audio", "segment")  # the cache's folders

_OFF = {"", "0", "off", "false", "no", "none"}
_STALE_TMP = 3600  # seconds before an abandoned temporary entry is swept
_SIZE = re.compile(r"^(\d+)([GgMm]?)$")
_UNITS = {"": 1, "M": 1024 ** 2, "G": 1024 ** 3}


def default_dir(env=None) -> Path | None:
    """The cache directory from the environment, or None when ``SLIDECAST_CACHE`` turns it off."""
    env = os.environ if env is None else env
    if "SLIDECAST_CACHE" in env:
        value = env["SLIDECAST_CACHE"].strip()
        return None if value.lower() in _OFF else Path(value).expanduser()
    base = env.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "slidecast"


def parse_size(text: str, name: str = "SLIDECAST_CACHE_MAX") -> int:
    """A size in bytes from ``12G``, ``800M`` or a plain byte count; ValueError for anything else, or zero."""
    m = _SIZE.match(text.strip())
    if not m or int(m.group(1)) == 0:
        raise ValueError(
            f"{name}={text!r} is not a cache size: use a byte count, or a whole number "
            "with a G or M suffix, such as 12G or 800M"
        )
    return int(m.group(1)) * _UNITS[m.group(2).upper()]


def format_size(n: int) -> str:
    """``n`` bytes as ``1.5 GB``, ``12.0 MB`` or ``300 B``."""
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} B"


def max_bytes_from_env(env=None) -> int:
    """The cap from ``SLIDECAST_CACHE_MAX``, else :data:`MAX_BYTES`; ValueError when the variable is bad."""
    env = os.environ if env is None else env
    value = env.get("SLIDECAST_CACHE_MAX", "").strip()
    return parse_size(value) if value else MAX_BYTES


def digest(*parts: Any) -> str:
    """A hash of ``parts`` (anything JSON can hold), tagged with the cache format version."""
    blob = json.dumps([VERSION, *parts], sort_keys=True, default=repr, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    """A hash of a file's bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def tts_identity(tts) -> str:
    """What makes one TTS provider speak differently from another: its class and every public setting.

    A provider whose output depends on something its attributes don't show can set a ``cache_key`` of its own.
    A setting that has no stable text form (an object's default repr) makes the key change every run, so the
    line is spoken again: a miss, never a wrong hit.
    """
    own = getattr(tts, "cache_key", None)
    if own is not None:
        return f"{type(tts).__qualname__}:{own}"
    settings = {k: repr(v) for k, v in sorted(vars(tts).items()) if not k.startswith("_")}
    return f"{type(tts).__module__}.{type(tts).__qualname__}:{json.dumps(settings, sort_keys=True)}"


class Cache:
    """Narration audio (a directory per line) and built segments (a file each) under one root."""

    def __init__(self, root: str | Path, max_bytes: int | None = None):
        self.root = Path(root)
        self.max_bytes = max_bytes_from_env() if max_bytes is None else max_bytes
        # Entries this cache object has served or stored: a render's own, which a prune must never delete,
        # however small the cap.
        self._used: set[Path] = set()

    @classmethod
    def of(cls, setting: str | Path | bool | Cache | None) -> Cache | None:
        """The cache a ``cache=`` argument asks for: None is the default, False is off, a path is that directory."""
        if isinstance(setting, Cache):
            return setting
        if setting is False:
            return None
        if setting is None or setting is True:
            root = default_dir()
            return cls(root) if root else None
        return cls(setting)

    # -- narration: audio file + its measured duration --------------------------------------------------------

    def get_audio(self, key: str, dest: Path) -> tuple | None:
        """Restore a narration into ``dest``; return ``(duration,)`` (duration may be None), or None on a miss."""
        entry = self.root / "audio" / key
        try:
            meta = json.loads((entry / "meta.json").read_text())
            _place(entry / "audio", dest)
        except (OSError, ValueError):
            return None
        _touch(entry)
        self._used.add(entry)
        return (meta["duration"],)

    def put_audio(self, key: str, src: Path, duration: float | None) -> None:
        final = self.root / "audio" / key
        self._used.add(final)
        if final.exists():
            return
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=final.parent))
        try:
            shutil.copyfile(src, tmp / "audio")
            (tmp / "meta.json").write_text(json.dumps({"duration": duration}))
            os.replace(tmp, final)
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)

    # -- segments: one .mkv each ------------------------------------------------------------------------------

    def get_segment(self, key: str, dest: Path) -> bool:
        entry = self.root / "segment" / f"{key}.mkv"
        try:
            _place(entry, dest)
        except OSError:
            return False
        _touch(entry)
        self._used.add(entry)
        return True

    def put_segment(self, key: str, src: Path) -> None:
        final = self.root / "segment" / f"{key}.mkv"
        self._used.add(final)
        if final.exists() or not Path(src).exists():
            return
        final.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=final.parent)
        os.close(fd)
        try:
            shutil.copyfile(src, tmp)
            os.replace(tmp, final)
        except OSError:
            Path(tmp).unlink(missing_ok=True)

    # -- upkeep -----------------------------------------------------------------------------------------------

    def status(self) -> dict[str, tuple[int, int]]:
        """``{folder: (entries, bytes)}`` for each of the audio and segment folders (abandoned writes left out)."""
        out = {}
        for kind in KINDS:
            entries = self._entries(kind)
            out[kind] = (len(entries), sum(size for _, size, _ in entries))
        return out

    def evict(self, max_bytes: int | None = None, dry_run: bool = False) -> list[tuple[Path, int]]:
        """Delete least recently used entries until the cache fits in ``max_bytes`` (default: this cache's cap).

        Returns each ``(path, bytes)`` deleted, oldest first; with ``dry_run`` it deletes nothing (not even an
        abandoned write) and returns what it would. An entry this cache object served or stored is never
        deleted, so a render's own segments survive a cap smaller than the render.
        """
        cap = self.max_bytes if max_bytes is None else max_bytes
        entries = [e for kind in KINDS for e in self._entries(kind)]
        if not dry_run:
            self._sweep_stale()
        total = sum(size for _, size, _ in entries)
        gone = []
        for _, size, entry in sorted(entries, key=lambda e: e[0]):
            if total <= cap:
                break
            if entry in self._used:
                continue
            if not dry_run:
                _remove(entry)
            total -= size
            gone.append((entry, size))
        return gone

    def prune(self) -> int:
        """Delete least recently used entries until the cache fits in ``max_bytes``; return how many went."""
        return len(self.evict())

    def _entries(self, kind: str) -> list[tuple[float, int, Path]]:
        """``(mtime, bytes, path)`` of each finished entry in a folder; a hit sets the mtime, so it is last use."""
        folder = self.root / kind
        found = []
        for entry in folder.glob("*") if folder.is_dir() else ():
            if entry.name.startswith(".tmp-"):
                continue
            try:
                found.append((entry.stat().st_mtime, _size(entry), entry))
            except OSError:
                continue
        return found

    def _sweep_stale(self) -> None:
        """Remove abandoned writes; a young one may be mid-write in another process."""
        now = time.time()
        for kind in KINDS:
            folder = self.root / kind
            for entry in folder.glob(".tmp-*") if folder.is_dir() else ():
                try:
                    if now - entry.stat().st_mtime > _STALE_TMP:
                        _remove(entry)
                except OSError:
                    continue


def _place(src: Path, dest: Path) -> None:
    """Hardlink ``src`` to ``dest`` (a copy where that can't be), replacing whatever is there."""
    Path(dest).unlink(missing_ok=True)
    try:
        os.link(src, dest)
    except OSError:
        shutil.copyfile(src, dest)


def _touch(path: Path) -> None:
    try:
        os.utime(path)
    except OSError:
        pass


def _size(path: Path) -> int:
    if path.is_dir():
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return path.stat().st_size


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
