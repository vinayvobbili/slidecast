import pytest


@pytest.fixture(autouse=True)
def no_default_cache(monkeypatch):
    """Keep tests out of the real ~/.cache/slidecast; the cache tests point SLIDECAST_CACHE at a tmp_path."""
    monkeypatch.setenv("SLIDECAST_CACHE", "off")
