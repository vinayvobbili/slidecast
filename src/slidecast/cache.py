"""A persistent cache of narration audio and built segments, so a rebuild only redoes what changed.

Speaking a line and encoding a segment are the slow steps of a render. Each result is stored under a hash of
everything that can change it, so an edit to two slides in a long reel redoes two slides and reuses the rest.
Entries are written to a temporary name and renamed into place, so an interrupted render never leaves a half-written
entry, and the least recently used ones are pruned once the cache grows past :data:`MAX_BYTES`.

The cache lives in ``$SLIDECAST_CACHE``, else ``$XDG_CACHE_HOME/slidecast``, else ``~/.cache/slidecast``. Set
``SLIDECAST_CACHE`` to ``off`` (or pass ``cache=False`` to :meth:`Reel.render`) to turn it off.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Optional, Union

# Bump to retire every entry when a change here or in video.py alters what a key doesn't see.
VERSION = 1

# Pruned down to this, least recently used first, at the end of a render.
MAX_BYTES = 5 * 1024 ** 3

# What the segment builders encode with. A change to video.py's settings goes here, so old segments aren't reused.
ENCODE = "libx264/stillimage/yuv420p;pcm_s16le/48000/2;aac/192k"

_OFF = {"", "0", "off", "false", "no", "none"}
_STALE_TMP = 3600  # seconds before an abandoned temporary entry is swept


def default_dir(env=None) -> Optional[Path]:
    """The cache directory from the environment, or None when ``SLIDECAST_CACHE`` turns it off."""
    env = os.environ if env is None else env
    if "SLIDECAST_CACHE" in env:
        value = env["SLIDECAST_CACHE"].strip()
        return None if value.lower() in _OFF else Path(value).expanduser()
    base = env.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "slidecast"


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

    def __init__(self, root: Union[str, Path], max_bytes: int = MAX_BYTES):
        self.root = Path(root)
        self.max_bytes = max_bytes

    @classmethod
    def of(cls, setting: Union[str, Path, bool, None, "Cache"]) -> Optional["Cache"]:
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

    def get_audio(self, key: str, dest: Path) -> Optional[tuple]:
        """Restore a narration into ``dest``; return ``(duration,)`` (duration may be None), or None on a miss."""
        entry = self.root / "audio" / key
        try:
            meta = json.loads((entry / "meta.json").read_text())
            _place(entry / "audio", dest)
        except (OSError, ValueError):
            return None
        _touch(entry)
        return (meta["duration"],)

    def put_audio(self, key: str, src: Path, duration: Optional[float]) -> None:
        final = self.root / "audio" / key
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
        return True

    def put_segment(self, key: str, src: Path) -> None:
        final = self.root / "segment" / f"{key}.mkv"
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

    def prune(self) -> int:
        """Delete least recently used entries until the cache fits in ``max_bytes``; return how many went."""
        now = time.time()
        entries = []
        for kind in ("audio", "segment"):
            for entry in (self.root / kind).glob("*") if (self.root / kind).is_dir() else ():
                try:
                    age = now - entry.stat().st_mtime
                    if entry.name.startswith(".tmp-"):
                        if age > _STALE_TMP:  # an abandoned write; a young one may be mid-write elsewhere
                            _remove(entry)
                        continue
                    entries.append((entry.stat().st_mtime, _size(entry), entry))
                except OSError:
                    continue
        total = sum(size for _, size, _ in entries)
        removed = 0
        for _, size, entry in sorted(entries, key=lambda e: e[0]):
            if total <= self.max_bytes:
                break
            _remove(entry)
            total -= size
            removed += 1
        return removed


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
