"""Territories with their own ISO 3166-1 code keep it (decision E15).

Nominatim reports Hong Kong and Macau under China, Puerto Rico and Guam under the United
States, and the French overseas regions under France, but reports Taiwan, Jersey or
Greenland under their own code. The text rules list Puerto Rico and Guam as US states.
Every result is therefore mapped to the territory's own code, whatever its source.
"""

import pytest

from github_collector.geocoding.geocoding_service import GeocodingService
from github_collector.geocoding.territories import apply_territory


class FakeClient:
    def __init__(self, answer):
        self.answer = answer

    def geocode(self, location):
        return self.answer


@pytest.fixture
def service(tmp_path):
    svc = GeocodingService(cache_file=str(tmp_path / "cache.json"))
    svc.rate_limiter.wait_for_api = lambda: None
    return svc


@pytest.mark.parametrize(
    "result, expected",
    [
        ({"country_code": "CN", "region": "Hong Kong", "formatted_address": None}, "HK"),
        (
            {
                "country_code": "CN",
                "region": "New Territories",
                "formatted_address": "Sha Tin, New Territories, Hong Kong, China",
            },
            "HK",
        ),
        ({"country_code": "cn", "region": "Macau", "formatted_address": "Macau, China"}, "MO"),
        ({"country_code": "US", "region": "Puerto Rico", "formatted_address": None}, "PR"),
        ({"country_code": "US", "region": "Guam", "formatted_address": None}, "GU"),
        ({"country_code": "FR", "region": "Réunion", "formatted_address": None}, "RE"),
        (
            {
                "country_code": "FR",
                "region": "South Province",
                "formatted_address": "Nouméa, South Province, New Caledonia, France",
            },
            "NC",
        ),
        ({"country_code": "NL", "region": "", "formatted_address": "Oranjestad, Aruba"}, "AW"),
    ],
)
def test_territory_gets_its_own_code(result, expected):
    assert apply_territory(result)["country_code"] == expected


@pytest.mark.parametrize(
    "result",
    [
        {"country_code": "CN", "region": "Guangdong", "formatted_address": "Shenzhen, China"},
        {"country_code": "US", "region": "Texas", "formatted_address": "Austin, Texas, USA"},
        # a Swiss canton that shares a word with "Wallis and Futuna"
        {"country_code": "CH", "region": "Wallis", "formatted_address": "Sion, Wallis"},
        # a street named after a territory is not the territory
        {
            "country_code": "FR",
            "region": "Île-de-France",
            "formatted_address": "Rue de la Guadeloupe, Paris",
        },
    ],
)
def test_other_results_are_unchanged(result):
    assert apply_territory(result) == result


def test_country_name_follows_the_code():
    result = apply_territory(
        {"country_code": "US", "country": "United States", "region": "Puerto Rico"}
    )
    assert result["country"] == "Puerto Rico"


def test_nominatim_result_is_mapped(service):
    service.nominatim_client = FakeClient(
        {
            "country_code": "us",
            "country": "United States",
            "address": {"state": "Puerto Rico"},
            "latitude": 18.4,
            "longitude": -66.1,
            "formatted_address": "San Juan, Puerto Rico, United States",
        }
    )
    assert service.geocode("Xqzv San Juan Plomb")["country_code"] == "PR"


def test_cached_result_is_mapped(service):
    service.cache.set(
        "Kowloon Tong",
        {
            "country_code": "CN",
            "region": "Kowloon",
            "formatted_address": "Kowloon, Hong Kong, China",
        },
    )
    assert service.geocode("Kowloon Tong")["country_code"] == "HK"


def test_text_rule_result_is_mapped(service):
    service.nominatim_client = FakeClient(None)
    assert service.geocode("Puerto Rico")["country_code"] == "PR"
