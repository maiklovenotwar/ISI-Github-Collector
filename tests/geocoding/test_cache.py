from __future__ import annotations

from github_collector.geocoding.cache import GeocodingCache


def test_cache_save_and_load_roundtrip(tmp_path):
    cache_file = tmp_path / "geocoding_cache.json"

    cache = GeocodingCache(cache_file)
    cache.set("Berlin", {"country_code": "DE"})
    cache.flush()

    assert cache_file.exists()

    # Reload cache from disk and verify contents
    cache2 = GeocodingCache(cache_file)
    result = cache2.get("Berlin")
    assert result == {"country_code": "DE"}


def test_cache_set_uses_normalized_keys(tmp_path):
    cache_file = tmp_path / "geocoding_cache.json"

    cache = GeocodingCache(cache_file)
    cache.set("  Berlin  ", {"country_code": "DE"})

    # Internal keys should be normalized (lowercase, trimmed, single spaces)
    assert "berlin" in cache.cache
    assert cache.get("BERLIN") == {"country_code": "DE"}


def test_cache_flush_creates_file(tmp_path):
    cache_file = tmp_path / "geocoding_cache.json"

    cache = GeocodingCache(cache_file)
    cache.set("Berlin", {"country_code": "DE"})
    cache.flush()

    assert cache_file.exists()
