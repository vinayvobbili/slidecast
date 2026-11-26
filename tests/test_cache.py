import os
import subprocess
import time
from pathlib import Path

import pytest

from slidecast import Reel, SilentTTS
from slidecast import cache as cache_mod
from slidecast.cache import Cache
from tests.fakes import FakeRenderer, FakeRunner


class CountingTTS:
    """Speaks a fixed second per line and remembers every line it was asked for."""

    def __init__(self, voice="v1"):
        self.voice = voice
        self._log = []  # private: the cache key reads public settings only

    @property
    def spoken(self):
        return self._log

    def synthesize(self, text, out_path):
        self._log.append(text)
        Path(out_path).write_bytes(f"{self.voice}:{text}".encode())  # a different line sounds different
        return 1.0


class VersionedRenderer(FakeRenderer):
    """Screenshots whose bytes change with ``look``, so the same HTML can render differently."""

    look = "a"

    def screenshot(self, html, out_path, *, width, height):
        super().screenshot(html, out_path, width=width, height=height)
        Path(out_path).write_bytes(f"{html}|{self.look}".encode())


@pytest.fixture
def runner(monkeypatch):
    fake = FakeRunner(touch_output=True)
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


def _built(runner):
    """How many segments ffmpeg was asked to build (the concat writes an .mp4)."""
    return sum(1 for c in runner.commands if c[-1].endswith(".mkv"))


def _reel(tts, renderer=None, narrations=("one", "two", "three")):
    reel = Reel(tts=tts, renderer=renderer or VersionedRenderer())
    for n, text in enumerate(narrations):
        reel.add(f"<h1>{n}</h1>", text)
    return reel


def _render(reel, tmp_path, cache, **kw):
    return reel.render(tmp_path / "out.mp4", ffmpeg="ff", cache=cache, **kw)


def test_unchanged_reel_builds_nothing_and_speaks_nothing(runner, tmp_path):
    tts = CountingTTS()
    cache = tmp_path / "cache"
    _render(_reel(tts), tmp_path, cache)
    assert len(tts.spoken) == 3 and _built(runner) == 3

    runner.commands.clear()
    seen = []
    _render(_reel(tts), tmp_path, cache, on_cached=lambda i, total, s: seen.append((i, total)))
    assert len(tts.spoken) == 3
    assert _built(runner) == 0
    assert seen == [(1, 3), (2, 3), (3, 3)]
    assert "concat" in runner.commands[-1]


def test_changed_narration_rebuilds_only_that_slide(runner, tmp_path):
    tts = CountingTTS()
    cache = tmp_path / "cache"
    _render(_reel(tts), tmp_path, cache)
    runner.commands.clear()
    tts.spoken.clear()
    _render(_reel(tts, narrations=("one", "TWO", "three")), tmp_path, cache)
    assert tts.spoken == ["TWO"]
    assert _built(runner) == 1


def test_changed_image_rebuilds_the_segment_but_reuses_the_audio(runner, tmp_path):
    tts = CountingTTS()
    cache = tmp_path / "cache"
    renderer = VersionedRenderer()
    _render(_reel(tts, renderer), tmp_path, cache)
    runner.commands.clear()
    tts.spoken.clear()
    renderer.look = "b"  # same HTML, different pixels
    _render(_reel(tts, renderer), tmp_path, cache)
    assert tts.spoken == []
    assert _built(runner) == 3
    runner.commands.clear()
    _render(_reel(tts, renderer), tmp_path, cache)
    assert _built(runner) == 0


def test_another_voice_does_not_reuse_the_audio(runner, tmp_path):
    cache = tmp_path / "cache"
    _render(_reel(CountingTTS("v1")), tmp_path, cache)
    other = CountingTTS("v2")
    _render(_reel(other), tmp_path, cache)
    assert len(other.spoken) == 3


def test_other_geometry_rebuilds_the_segments(runner, tmp_path):
    tts = CountingTTS()
    cache = tmp_path / "cache"
    _render(_reel(tts), tmp_path, cache)
    runner.commands.clear()
    reel = _reel(tts)
    reel.width, reel.height = 640, 360
    _render(reel, tmp_path, cache)
    assert _built(runner) == 3 and len(tts.spoken) == 3


def test_another_quality_rebuilds_the_segments(runner, tmp_path):
    tts = CountingTTS()
    cache = tmp_path / "cache"
    _render(_reel(tts), tmp_path, cache)
    runner.commands.clear()
    reel = _reel(tts)
    reel.video_crf = 17
    _render(reel, tmp_path, cache)
    assert _built(runner) == 3 and len(tts.spoken) == 3
    runner.commands.clear()
    _render(reel, tmp_path, cache)
    assert _built(runner) == 0


def test_the_default_cache_key_is_unchanged_by_the_quality_option():
    assert cache_mod.encode_key() == cache_mod.ENCODE
    assert cache_mod.encode_key(16) != cache_mod.ENCODE


def test_cache_false_keeps_and_reuses_nothing(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("SLIDECAST_CACHE", str(tmp_path / "env-cache"))
    tts = CountingTTS()
    _render(_reel(tts), tmp_path, False)
    _render(_reel(tts), tmp_path, False)
    assert len(tts.spoken) == 6 and _built(runner) == 6
    assert not (tmp_path / "env-cache").exists()


def test_env_var_sets_the_default_cache(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("SLIDECAST_CACHE", str(tmp_path / "env-cache"))
    tts = CountingTTS()
    _render(_reel(tts), tmp_path, None)
    _render(_reel(tts), tmp_path, None)
    assert len(tts.spoken) == 3
    assert (tmp_path / "env-cache" / "segment").is_dir()


def test_default_dir_follows_xdg_and_can_be_switched_off(tmp_path):
    assert cache_mod.default_dir({"XDG_CACHE_HOME": str(tmp_path)}) == tmp_path / "slidecast"
    assert cache_mod.default_dir({"HOME": "/x"}) == Path.home() / ".cache" / "slidecast"
    assert cache_mod.default_dir({"SLIDECAST_CACHE": "~/sc"}) == Path.home() / "sc"
    assert cache_mod.default_dir({"SLIDECAST_CACHE": "off", "XDG_CACHE_HOME": "/x"}) is None
    assert Cache.of(None) is None  # the autouse fixture sets SLIDECAST_CACHE=off


def test_clip_segment_is_cached_by_the_video_bytes(runner, monkeypatch, tmp_path):
    from slidecast import video

    monkeypatch.setattr(video, "probe_duration", lambda *a, **k: 2.0)
    clip = tmp_path / "demo.mp4"
    clip.write_bytes(b"video-1")
    cache = tmp_path / "cache"

    def reel():
        r = Reel(tts=CountingTTS(), renderer=VersionedRenderer())
        r.add_clip(clip, "watch")
        return r

    _render(reel(), tmp_path, cache)
    assert _built(runner) == 1
    _render(reel(), tmp_path, cache)
    assert _built(runner) == 1
    clip.write_bytes(b"video-2")
    _render(reel(), tmp_path, cache)
    assert _built(runner) == 2


def test_prune_removes_the_least_recently_used_over_the_cap(tmp_path):
    cache = Cache(tmp_path, max_bytes=250)
    for n, name in enumerate(["old", "mid", "new"]):
        src = tmp_path / f"{name}.mkv"
        src.write_bytes(b"x" * 100)
        cache.put_segment(name, src)
        stamp = time.time() - 1000 + n * 100
        os.utime(tmp_path / "segment" / f"{name}.mkv", (stamp, stamp))
    cache = Cache(tmp_path, max_bytes=250)  # a later render: the entries above are not its own
    assert cache.get_segment("mid", tmp_path / "out.mkv")  # a hit makes "mid" the newest
    assert cache.prune() == 1
    left = sorted(p.name for p in (tmp_path / "segment").iterdir())
    assert left == ["mid.mkv", "new.mkv"]


def test_prune_sweeps_abandoned_temporaries_only(tmp_path):
    cache = Cache(tmp_path)
    seg = tmp_path / "segment"
    seg.mkdir()
    old, young = seg / ".tmp-old", seg / ".tmp-young"
    old.write_bytes(b"x")
    young.write_bytes(b"x")
    stamp = time.time() - 7200
    os.utime(old, (stamp, stamp))
    cache.prune()
    assert not old.exists() and young.exists()


def test_interrupted_write_leaves_no_entry(tmp_path, monkeypatch):
    cache = Cache(tmp_path / "cache")
    src = tmp_path / "s.mkv"
    src.write_bytes(b"x" * 10)
    aud = tmp_path / "a.wav"
    aud.write_bytes(b"y" * 10)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(cache_mod.shutil, "copyfile", boom)
    cache.put_segment("k", src)
    cache.put_audio("k", aud, 1.0)
    monkeypatch.undo()

    assert not cache.get_segment("k", tmp_path / "o.mkv")
    assert cache.get_audio("k", tmp_path / "o.wav") is None
    assert not [p for p in (tmp_path / "cache").rglob("*") if p.is_file()]


def test_audio_roundtrip_keeps_an_unknown_duration(tmp_path):
    cache = Cache(tmp_path / "cache")
    src = tmp_path / "a.mp3"
    src.write_bytes(b"ID3")
    cache.put_audio("k", src, None)
    dest = tmp_path / "back.mp3"
    assert cache.get_audio("k", dest) == (None,)
    assert dest.read_bytes() == b"ID3"


def test_tts_identity_sees_public_settings_and_honors_cache_key():
    a, b = CountingTTS("v1"), CountingTTS("v2")
    assert cache_mod.tts_identity(a) != cache_mod.tts_identity(b)
    wide = cache_mod.tts_identity(SilentTTS(seconds=1, sample_rate=8000))
    assert wide != cache_mod.tts_identity(SilentTTS(seconds=1))
    b.cache_key = "model-9"
    assert cache_mod.tts_identity(b).endswith(":model-9")


def test_parse_size_reads_gigabytes_megabytes_and_plain_counts():
    assert cache_mod.parse_size("12G") == 12 * 1024 ** 3
    assert cache_mod.parse_size("800M") == 800 * 1024 ** 2
    assert cache_mod.parse_size("4096") == 4096
    assert cache_mod.parse_size(" 3g ") == 3 * 1024 ** 3


@pytest.mark.parametrize("text", ["12GB", "1.5G", "abc", "-5M", "0G", "0", "G"])
def test_parse_size_rejects_anything_else(text):
    with pytest.raises(ValueError, match="SLIDECAST_CACHE_MAX"):
        cache_mod.parse_size(text)


def test_cap_defaults_to_five_gigabytes_when_unset_or_empty(monkeypatch):
    monkeypatch.delenv("SLIDECAST_CACHE_MAX", raising=False)
    assert cache_mod.max_bytes_from_env() == cache_mod.MAX_BYTES == 2 * 1024 ** 3
    assert cache_mod.max_bytes_from_env({"SLIDECAST_CACHE_MAX": "  "}) == cache_mod.MAX_BYTES


def test_bad_cap_raises_when_the_cache_is_built(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIDECAST_CACHE_MAX", "twelve")
    with pytest.raises(ValueError, match="SLIDECAST_CACHE_MAX='twelve'"):
        Cache(tmp_path)
    with pytest.raises(ValueError):
        Cache.of(tmp_path)


def test_explicit_max_bytes_wins_over_the_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIDECAST_CACHE_MAX", "not-a-size")
    assert Cache(tmp_path, max_bytes=99).max_bytes == 99


def test_prune_honours_the_env_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIDECAST_CACHE_MAX", "250")
    cache = Cache(tmp_path)
    assert cache.max_bytes == 250
    for n, name in enumerate(["old", "mid", "new"]):
        src = tmp_path / f"{name}.mkv"
        src.write_bytes(b"x" * 100)
        cache.put_segment(name, src)
        stamp = time.time() - 1000 + n * 100
        os.utime(tmp_path / "segment" / f"{name}.mkv", (stamp, stamp))
    assert Cache(tmp_path).prune() == 1  # a later render
    assert sorted(p.name for p in (tmp_path / "segment").iterdir()) == ["mid.mkv", "new.mkv"]

    monkeypatch.setenv("SLIDECAST_CACHE_MAX", "1G")
    assert Cache(tmp_path).prune() == 0


def test_a_render_prunes_an_oversized_cache_before_it_starts(runner, tmp_path):
    cache_dir = tmp_path / "cache"
    seg = cache_dir / "segment"
    seg.mkdir(parents=True)
    old = seg / "oldentry.mkv"
    old.write_bytes(b"x" * 1000)
    stamp = time.time() - 5000
    os.utime(old, (stamp, stamp))

    seen = []
    real_prune = Cache.prune

    def spy(self):
        seen.append(old.exists())  # what is on disk when each prune runs
        return real_prune(self)

    Cache.prune = spy
    try:
        _render(_reel(CountingTTS()), tmp_path, Cache(cache_dir, max_bytes=500))
    finally:
        Cache.prune = real_prune
    assert seen[0] is True  # the first prune ran before the render built anything
    assert not old.exists()
    assert len(seen) == 2  # and again at the end


def _fill(cache, names, size=100):
    """Segments of ``size`` bytes, the first name the oldest by 100 s each."""
    for n, name in enumerate(names):
        src = cache.root / f"{name}.src"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"x" * size)
        cache.put_segment(name, src)
        src.unlink()
        stamp = time.time() - 1000 + n * 100
        os.utime(cache.root / "segment" / f"{name}.mkv", (stamp, stamp))
    cache._used.clear()  # as if a later render opened this cache


def test_status_counts_entries_and_bytes_per_folder(tmp_path):
    cache = Cache(tmp_path / "cache", max_bytes=10_000)
    assert cache.status() == {"audio": (0, 0), "segment": (0, 0)}
    _fill(cache, ["a", "b"], size=100)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"y" * 40)
    cache.put_audio("line", wav, 1.0)
    status = cache.status()
    assert status["segment"] == (2, 200)
    assert status["audio"][0] == 1 and status["audio"][1] >= 40  # the audio and its meta.json


def test_format_size_picks_a_unit():
    assert cache_mod.format_size(300) == "300 B"
    assert cache_mod.format_size(1536) == "1.5 KB"
    assert cache_mod.format_size(5 * 1024 ** 2) == "5.0 MB"
    assert cache_mod.format_size(12 * 1024 ** 3) == "12.0 GB"


def test_evict_with_a_cap_of_its_own_and_dry_run_deletes_nothing(tmp_path):
    cache = Cache(tmp_path / "cache", max_bytes=10_000)  # roomy: the default cap would remove nothing
    _fill(cache, ["old", "mid", "new"])
    plan = cache.evict(max_bytes=150, dry_run=True)
    assert [(p.name, size) for p, size in plan] == [("old.mkv", 100), ("mid.mkv", 100)]
    assert len(list((tmp_path / "cache" / "segment").iterdir())) == 3
    done = cache.evict(max_bytes=150)
    assert done == plan
    assert [p.name for p in (tmp_path / "cache" / "segment").iterdir()] == ["new.mkv"]


def test_dry_run_leaves_an_abandoned_write_alone(tmp_path):
    cache = Cache(tmp_path, max_bytes=1000)
    seg = tmp_path / "segment"
    seg.mkdir()
    old = seg / ".tmp-old"
    old.write_bytes(b"x")
    stamp = time.time() - 7200
    os.utime(old, (stamp, stamp))
    cache.evict(dry_run=True)
    assert old.exists()
    cache.evict()
    assert not old.exists()


def test_prune_never_deletes_what_this_render_used(tmp_path):
    cache = Cache(tmp_path / "cache", max_bytes=150)
    _fill(cache, ["old", "mid", "new"])
    # The render hits the oldest entry (which also makes it the newest), then stores a segment of its own.
    assert cache.get_segment("old", tmp_path / "out.mkv")
    fresh = tmp_path / "fresh.mkv"
    fresh.write_bytes(b"z" * 100)
    cache.put_segment("fresh", fresh)
    os.utime(tmp_path / "cache" / "segment" / "fresh.mkv", (1, 1))  # even if its clock says it is the oldest
    cache.prune()
    left = sorted(p.name for p in (tmp_path / "cache" / "segment").iterdir())
    assert "old.mkv" in left and "fresh.mkv" in left  # over the cap, but nothing the render used went
    assert "mid.mkv" not in left


def test_a_hit_on_an_entry_that_already_exists_protects_it_too(tmp_path):
    cache = Cache(tmp_path / "cache", max_bytes=100)
    _fill(cache, ["old", "new"])
    src = tmp_path / "again.mkv"
    src.write_bytes(b"x" * 100)
    cache.put_segment("old", src)  # already stored: put returns early, but the render still needs it
    os.utime(tmp_path / "cache" / "segment" / "old.mkv", (1, 1))
    cache.prune()
    assert [p.name for p in (tmp_path / "cache" / "segment").iterdir()] == ["old.mkv"]
