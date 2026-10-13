"""Tests for the evaluation step of scripts/maintenance/benchmark_geocoding.py."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "maintenance" / "benchmark_geocoding.py"


@pytest.fixture(scope="module")
def module():
    spec = importlib.util.spec_from_file_location("benchmark_geocoding_under_test", SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


class FakeService:
    def __init__(self, answers, unanswered=()):
        self.answers = answers
        self.unanswered = set(unanswered)
        self.last_request_unanswered = False

    def geocode(self, location):
        self.last_request_unanswered = location in self.unanswered
        return {} if self.last_request_unanswered else self.answers.get(location, {})


def new_counters():
    return {"correct": 0, "tp": 0, "tn": 0, "fp": 0, "fn": 0, "mismatches": 0}


def test_results_are_counted_by_source_and_unanswered_requests_separately(module, capsys):
    service = FakeService(
        {
            "Germany": {"country_code": "DE", "source": "heuristic"},
            "Karlsruhe xy": {"country_code": "DE", "source": "nominatim"},
            "Paris xy": {"country_code": "US", "source": "nominatim"},
        },
        unanswered={"Lyon xy"},
    )
    counters, errors = new_counters(), []
    for location, expected in [
        ("Germany", "DE"),
        ("Karlsruhe xy", "DE"),
        ("Paris xy", "FR"),
        ("Lyon xy", "FR"),
        ("Nowhere", None),
    ]:
        module._evaluate_sample(location, expected, service, counters, errors)

    assert (counters["correct"], counters["mismatches"], counters["fn"]) == (3, 1, 1)
    assert (counters["source_heuristic_total"], counters["source_heuristic_correct"]) == (1, 1)
    assert (counters["source_nominatim_total"], counters["source_nominatim_correct"]) == (2, 1)
    assert counters["source_unanswered_total"] == 1
    assert counters["source_no_result_total"] == 1

    module._print_report(5, counters, errors)
    out = capsys.readouterr().out
    assert "unanswered" in out and "nominatim    1 / 2" in out
