"""Tests for scripts/diagnostics/inspect_geocoding_cache.py (reads a local file only)."""

import importlib.util
import json
from pathlib import Path

import pytest

from github_collector.geocoding.heuristics import HeuristicResolver

SCRIPT = (
    Path(__file__).parent.parent.parent / "scripts" / "diagnostics" / "inspect_geocoding_cache.py"
)

CACHE = {
    "berlin xy": {
        "country_code": "DE",
        "source": "nominatim",
        "cached_at": "2026-10-12T00:00:00+00:00",
    },
    "old entry": {"country_code": "FR"},
    "germany": {},  # the text heuristic resolves this today
    "earth": {},  # recognised as not a place
    "xqzv plomb": {},  # neither: outage or genuine "no match"
    "wrtz kvelm": {"source": "no_result", "cached_at": "2026-10-12T00:00:00+00:00"},
}


@pytest.fixture(scope="module")
def module():
    spec = importlib.util.spec_from_file_location("inspect_geocoding_cache_under_test", SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_summary_separates_the_kinds_of_empty_entries(module):
    summary = module.summarize(CACHE, HeuristicResolver())

    assert (summary["total"], summary["with_country"], summary["empty"]) == (6, 2, 4)
    assert summary["empty_resolvable_offline"] == 1
    assert summary["empty_non_geographic"] == 1
    assert summary["empty_other"] == 2
    assert sorted(summary["other_keys"]) == ["wrtz kvelm", "xqzv plomb"]
    assert summary["without_metadata"] == 4
    assert summary["sources"] == {"nominatim": 1, "no_result": 1}


def test_main_reads_the_file_and_changes_nothing(module, tmp_path, capsys):
    path = tmp_path / "cache.json"
    content = json.dumps(CACHE)
    path.write_text(content, encoding="utf-8")

    assert module.main(["--cache-file", str(path), "--examples", "2"]) == 0

    out = capsys.readouterr().out
    assert "Einträge gesamt:          6" in out
    assert "sonstige:             2" in out
    assert path.read_text(encoding="utf-8") == content


def test_missing_file_exits_with_two(module, tmp_path):
    assert module.main(["--cache-file", str(tmp_path / "absent.json")]) == 2
