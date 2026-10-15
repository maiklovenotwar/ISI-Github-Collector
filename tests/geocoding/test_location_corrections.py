"""The table of corrections overrides wrong geocoder matches in the cache (K12, decision E14).

The cache held geocoder matches in the wrong country for strings that name their country
("Istanbul/Turkey" in Italy) and countries for strings that are not a place ("Antarctica"
in Canada). Each correction was checked by hand; it takes precedence over the cache, also
where it states that the string has no country.
"""

import csv

import pycountry
import pytest

from github_collector.geocoding.cache import GeocodingCache
from github_collector.geocoding.geocoding_service import GeocodingService
from github_collector.geocoding.heuristics import CORRECTIONS_FILE, HeuristicResolver
from github_collector.geocoding.normalization import normalize_location


def table_rows():
    with CORRECTIONS_FILE.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class NoClient:
    def geocode(self, location):
        raise AssertionError("Nominatim must not be asked")


@pytest.fixture
def service(tmp_path):
    svc = GeocodingService(cache_file=str(tmp_path / "cache.json"))
    svc.nominatim_client = NoClient()
    svc.rate_limiter.wait_for_api = lambda: None
    return svc


def test_table_is_well_formed():
    rows = table_rows()
    assert rows and list(rows[0]) == ["location", "country_code", "reason"]
    locations = [row["location"] for row in rows]
    assert len(locations) == len(set(locations))
    for row in rows:
        # stored in normalised form, as the service looks it up
        assert row["location"] == normalize_location(row["location"])
        code = row["country_code"]
        assert code == "" or pycountry.countries.get(alpha_2=code) is not None, row
        assert row["reason"], row


def test_table_does_not_repeat_or_contradict_the_built_in_entries():
    built_in = HeuristicResolver._problematic_location_map
    aliases = HeuristicResolver._country_aliases
    for row in table_rows():
        assert row["location"] not in built_in, row
        assert row["location"] not in aliases, row


def test_resolver_returns_the_correction():
    resolver = HeuristicResolver()
    assert resolver.resolve_mapping("istanbul/turkey")["country_code"] == "TR"
    assert resolver.resolve_correction("antarctica") == {}
    assert resolver.resolve_correction("berlin") is None


def test_correction_with_a_country_overrides_the_cache(service):
    service.cache.set("Istanbul/Turkey", {"country_code": "IT", "country": "Italy"})
    assert service.geocode("Istanbul/Turkey")["country_code"] == "TR"


def test_correction_without_a_country_overrides_the_cache(service):
    service.cache.set("Antarctica", {"country_code": "CA", "country": "Canada"})
    assert service.geocode("Antarctica") == {}
    assert GeocodingCache.is_empty_result(service.cache.get("Antarctica"))
