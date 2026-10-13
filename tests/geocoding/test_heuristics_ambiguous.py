"""US state rules must not claim places that only share a name with a US state.

The text rules run before the geocoder. Before this was fixed, any string ending in the
name or abbreviation of a US state was assigned to the United States: `Tbilisi, Georgia`,
`Perth, WA` (Western Australia) and `London, CA`. For the names and abbreviations that are
ambiguous in this way, the text rules now decide only when the text before them is a
known US city or state; otherwise the geocoder decides.
"""

import pytest

from github_collector.geocoding.heuristics import HeuristicResolver
from github_collector.geocoding.normalization import normalize_location


@pytest.fixture(scope="module")
def resolver():
    return HeuristicResolver()


def text_rule(resolver, location):
    return resolver.extract_country_from_text(normalize_location(location))[0]


@pytest.mark.parametrize(
    "location",
    [
        "Tbilisi, Georgia",
        "Batumi Georgia",
        "Georgia, Tbilisi",
        "Perth, WA",
        "Fremantle WA",
        "London, CA",
        "Toronto, CA",
        "Fresno, CA",  # a US place, but not one the rules know: the geocoder decides
    ],
)
def test_ambiguous_ending_with_unknown_prefix_is_left_to_the_geocoder(resolver, location):
    assert text_rule(resolver, location) is None


@pytest.mark.parametrize(
    "location",
    [
        "Atlanta, Georgia",
        "Atlanta, GA",
        "Seattle, WA",
        "San Francisco, CA",
        "Los Angeles CA",
        "Washington, WA",
        "California, CA",
    ],
)
def test_known_us_city_or_state_before_an_ambiguous_ending_is_us(resolver, location):
    assert text_rule(resolver, location) == "US"


@pytest.mark.parametrize("location", ["Georgia", "georgia"])
def test_bare_georgia_stays_with_the_us_state(resolver, location):
    # Ambiguous and not decidable from the text; the documented convention is the US state.
    assert text_rule(resolver, location) == "US"


@pytest.mark.parametrize(
    "location, expected",
    [
        ("Paris, Texas", "US"),
        ("Austin, TX", "US"),
        ("Cambridge, MA", "US"),
        ("Indiana", "US"),
        ("Berlin, DE", "DE"),
        ("Bangalore, IN", "IN"),
        ("Lima, Peru", None),
        ("Cambridge, UK", None),
    ],
)
def test_unambiguous_cases_are_unchanged(resolver, location, expected):
    assert text_rule(resolver, location) == expected
