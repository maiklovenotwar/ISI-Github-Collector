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
    # a geocoder result (with coordinates) is not replaced by a text rule
    service.cache.set(
        "Broomfield, CO",
        {"country_code": "US", "country": "United States", "latitude": 39.9, "longitude": -105.1},
    )
    assert service.geocode("Broomfield, CO")["country_code"] == "US"


class FakeClient:
    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def geocode(self, location):
        self.asked.append(location)
        return self.answer


def test_rule_result_in_the_cache_is_recomputed_after_a_rule_change(service):
    # Written by the old ending rule (no coordinates): "Boise, ID" as Indonesia. The current
    # rules leave the string to the geocoder (K14), so the old entry must not decide.
    service.cache.set("Boise, ID", {"country_code": "ID", "country": "Indonesia", "latitude": None})
    service.nominatim_client = FakeClient(
        {
            "country_code": "us",
            "country": "United States",
            "address": {"state": "Idaho"},
            "latitude": 43.6,
            "longitude": -116.2,
            "formatted_address": "Boise, Ada County, Idaho, United States",
        }
    )
    assert service.geocode("Boise, ID")["country_code"].upper() == "US"
    assert service.nominatim_client.asked == ["Boise, ID"]


def test_rule_result_in_the_cache_that_still_holds_needs_no_request(service):
    service.cache.set(
        "Austin, TX", {"country_code": "US", "country": "United States", "latitude": None}
    )
    assert service.geocode("Austin, TX")["country_code"] == "US"


def test_geocoder_result_in_the_cache_is_kept(service):
    service.cache.set(
        "Boise, ID",
        {"country_code": "US", "country": "United States", "latitude": 43.6, "longitude": -116.2},
    )
    assert service.geocode("Boise, ID")["country_code"] == "US"
