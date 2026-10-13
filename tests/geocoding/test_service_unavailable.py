"""An outage of the geocoding service must not be stored as "this location has no country".

Before this was fixed, a timeout was returned like an answer without a match, cached without
an expiry date, and every later account with the same location string received no country.
"""

import json

import pytest
from geopy.exc import GeocoderQuotaExceeded, GeocoderTimedOut

from github_collector.geocoding import nominatim_client
from github_collector.geocoding.cache import GeocodingCache
from github_collector.geocoding.geocoding_service import GeocodingService
from github_collector.geocoding.nominatim_client import GeocodingUnavailableError, NominatimClient

# Strings that neither the mapping table nor the text heuristics resolve, so that the
# request reaches the (fake) Nominatim client.
UNKNOWN_PLACE = "Xqzv Plomb"
OTHER_PLACE = "Wrtz Kvelm"


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.answer = None
        self.raise_unavailable = False

    def geocode(self, location):
        self.calls += 1
        if self.raise_unavailable:
            raise GeocodingUnavailableError("timeout")
        return self.answer


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def service(tmp_path, client):
    svc = GeocodingService(cache_file=str(tmp_path / "cache.json"))
    svc.nominatim_client = client
    svc.rate_limiter.wait_for_api = lambda: None
    return svc


def test_timeout_is_not_cached(service, client):
    client.raise_unavailable = True

    assert service.geocode(UNKNOWN_PLACE) == {}

    assert client.calls == 1
    assert service.cache.get(UNKNOWN_PLACE) is None
    assert service.unavailable_count == 1
    assert service.last_request_unanswered is True


def test_location_is_asked_again_after_an_outage(service, client):
    client.raise_unavailable = True
    service.geocode(UNKNOWN_PLACE)
    client.raise_unavailable = False
    client.answer = {"country_code": "de", "country": "Germany", "address": {}}

    assert service.geocode(UNKNOWN_PLACE)["country_code"] == "de"
    assert client.calls == 2
    assert service.last_request_unanswered is False


def test_answer_without_match_is_cached_as_no_result(service, client):
    assert service.geocode(UNKNOWN_PLACE) == {}

    entry = service.cache.get(UNKNOWN_PLACE)
    assert entry["source"] == "no_result"
    assert entry["cached_at"].endswith("+00:00")
    assert service.last_request_unanswered is False


def test_cached_no_result_is_returned_as_empty_dict_without_a_second_request(service, client):
    assert service.geocode(UNKNOWN_PLACE) == {}
    assert service.geocode(UNKNOWN_PLACE) == {}

    assert client.calls == 1


def test_new_entries_carry_source_and_timestamp(service, client):
    client.answer = {"country_code": "de", "country": "Germany", "address": {}}

    service.geocode(UNKNOWN_PLACE)

    entry = service.cache.get(UNKNOWN_PLACE)
    assert entry["source"] == "nominatim"
    assert entry["cached_at"].endswith("+00:00")
    assert entry["country_code"] == "de"


def test_heuristic_result_is_marked_as_such(service, client):
    result = service.geocode("Germany")

    assert result["country_code"] == "DE"
    assert client.calls == 0
    assert service.cache.get("Germany")["source"] in ("heuristic", "mapping")


def test_old_cache_entries_without_metadata_are_still_read(tmp_path):
    path = tmp_path / "old.json"
    content = json.dumps({"berlin": {"country_code": "DE"}, "xqzv plomb": {}})
    path.write_text(content, encoding="utf-8")

    cache = GeocodingCache(str(path))

    assert cache.get("Berlin")["country_code"] == "DE"
    assert cache.get(UNKNOWN_PLACE) == {}
    assert GeocodingCache.is_empty_result(cache.get(UNKNOWN_PLACE)) is True
    assert GeocodingCache.is_empty_result(cache.get("Berlin")) is False
    assert path.read_text(encoding="utf-8") == content  # loading does not rewrite the file


def test_old_empty_cache_entry_is_served_without_a_request(tmp_path, client):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"xqzv plomb": {}}), encoding="utf-8")
    svc = GeocodingService(cache_file=str(path))
    svc.nominatim_client = client

    assert svc.geocode(UNKNOWN_PLACE) == {}
    assert client.calls == 0


def test_drop_empty_removes_only_entries_without_country(tmp_path):
    cache = GeocodingCache(str(tmp_path / "c.json"))
    cache.set("Berlin", {"country_code": "DE"})
    cache.set(UNKNOWN_PLACE, {})
    cache.set(OTHER_PLACE, {"source": "no_result", "cached_at": "2026-10-12T00:00:00+00:00"})

    assert cache.drop_empty() == 2

    assert cache.get("Berlin")["country_code"] == "DE"
    assert cache.get(UNKNOWN_PLACE) is None and cache.get(OTHER_PLACE) is None


class FakeGeolocator:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def geocode(self, location, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def real_client(monkeypatch):
    monkeypatch.setattr(nominatim_client.time, "sleep", lambda seconds: None)
    return NominatimClient(user_agent="test")


@pytest.mark.parametrize(
    "error", [GeocoderTimedOut("slow"), GeocoderQuotaExceeded("429"), RuntimeError("boom")]
)
def test_client_gives_up_after_one_retry(real_client, error):
    real_client._geolocator = FakeGeolocator([error, error])

    with pytest.raises(GeocodingUnavailableError):
        real_client.geocode("Karlsruhe")

    assert real_client._geolocator.calls == 2


def test_client_succeeds_on_the_retry(real_client):
    class Location:
        raw = {"address": {"country_code": "de", "country": "Germany"}, "lat": "49", "lon": "8"}

    real_client._geolocator = FakeGeolocator([GeocoderTimedOut("slow"), Location()])

    assert real_client.geocode("Karlsruhe")["country_code"] == "DE"
    assert real_client._geolocator.calls == 2


def test_client_returns_none_only_when_the_service_answered_without_a_match(real_client):
    real_client._geolocator = FakeGeolocator([None])

    assert real_client.geocode("Xqzv") is None
    assert real_client._geolocator.calls == 1
