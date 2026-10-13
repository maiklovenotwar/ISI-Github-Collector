"""Tests for scripts/diagnostics/count_excluded_forks.py (the GitHub search is replaced)."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "diagnostics" / "count_excluded_forks.py"


@pytest.fixture(scope="module")
def module():
    spec = importlib.util.spec_from_file_location("count_excluded_forks_under_test", SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


class FakeApi:
    def __init__(self, totals, incomplete=()):
        self.totals = totals
        self.incomplete = set(incomplete)
        self.calls = []

    def search_repositories(self, query, per_page, page):
        self.calls.append((query, per_page, page))
        return {"total_count": self.totals[query], "incomplete_results": query in self.incomplete}


def test_queries_separate_forks_from_the_default_search(module):
    assert module.build_queries([10]) == [
        (10, "forks", "stars:>=10 fork:only"),
        (10, "non_forks", "stars:>=10"),
    ]


def test_two_requests_per_threshold_and_a_table(module, capsys):
    api = FakeApi(
        {
            "stars:>=10 fork:only": 5_000,
            "stars:>=10": 2_500_000,
            "stars:>=100 fork:only": 400,
            "stars:>=100": 400_000,
        }
    )

    assert module.main(["--min-stars", "10", "--min-stars", "100"], api=api) == 0

    assert len(api.calls) == 4
    assert all(per_page == 1 and page == 1 for _query, per_page, page in api.calls)
    out = capsys.readouterr().out
    assert "5,000" in out and "2,500,000" in out
    assert "0.2" in out and "0.1" in out
    assert "Näherungswert" in out
    assert "unvollständig" not in out


def test_incomplete_answer_is_mentioned(module, capsys):
    api = FakeApi({"stars:>=1 fork:only": 9, "stars:>=1": 90}, incomplete={"stars:>=1"})

    assert module.main(["--min-stars", "1"], api=api) == 0

    assert "unvollständig markiert" in capsys.readouterr().out
