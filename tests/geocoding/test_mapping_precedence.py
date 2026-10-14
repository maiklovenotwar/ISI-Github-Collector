"""A curated mapping that names a country outranks an older cache entry (K10).

The cache held a Nominatim match in Australia for "Korea, South"; the later mapping to KR
never took effect, because the cache was read first.
"""

import pytest

from github_collector.geocoding.geocoding_service import GeocodingService

AUSTRALIA = {"country_code": "AU", "country": "Australia", "region": "New South Wales"}


class NoClient:
    def geocode(self, location):
        raise AssertionError("Nominatim must not be asked")


@pytest.fixture
def service(tmp_path):
    svc = GeocodingService(cache_file=str(tmp_path / "cache.json"))
    svc.nominatim_client = NoClient()
    svc.rate_limiter.wait_for_api = lambda: None
    return svc


@pytest.mark.parametrize("location", ["Korea, South", "South, Korea"])
def test_mapping_with_country_overrides_stale_cache(service, location):
    service.cache.set(location, dict(AUSTRALIA))
    assert service.geocode(location)["country_code"] == "KR"


def test_mapping_correction_replaces_the_cache_entry(service):
    service.cache.set("Korea, South", dict(AUSTRALIA))
    service.geocode("Korea, South")
    assert service.cache.get("Korea, South")["country_code"] == "KR"


def test_cache_still_wins_over_a_not_a_place_mapping(service):
    # "de" is mapped to "not a place", but an older cache entry with DE stays in force.
    service.cache.set("de", {"country_code": "DE", "country": "Germany", "region": None})
    assert service.geocode("de")["country_code"] == "DE"


def test_cache_still_wins_over_text_rules(service):
    service.cache.set("Broomfield, CO", {"country_code": "US", "country": "United States"})
    assert service.geocode("Broomfield, CO")["country_code"] == "US"
