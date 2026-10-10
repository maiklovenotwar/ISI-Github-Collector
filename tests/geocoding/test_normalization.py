from __future__ import annotations

import pytest

from github_collector.geocoding.normalization import normalize_location


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("Berlin", "berlin"),
        ("  Berlin  ", "berlin"),
        ("BERLIN", "berlin"),
        ("San   Francisco", "san francisco"),
        ("  New\nYork\tCity  ", "new york city"),
    ],
)
def test_normalize_location_basic_cases(raw, expected):
    assert normalize_location(raw) == expected


def test_normalize_location_non_string_input():
    class Dummy:
        pass

    assert normalize_location(Dummy()) == ""
