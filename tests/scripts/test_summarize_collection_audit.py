"""Tests for scripts/diagnostics/summarize_collection_audit.py (reads a local file only)."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).parent.parent.parent
    / "scripts"
    / "diagnostics"
    / "summarize_collection_audit.py"
)


@pytest.fixture(scope="module")
def module():
    spec = importlib.util.spec_from_file_location("summarize_collection_audit_under_test", SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def entry(total, collected, shortfall=0, incomplete_pages=0, truncated=False):
    return {
        "finished_at": "2026-10-12T10:00:00+00:00",
        "start": "2024-01-01T00:00:00+00:00",
        "end": "2024-01-31T00:00:00+00:00",
        "min_stars": 1,
        "max_stars": 5,
        "total_count": total,
        "collected": collected,
        "first_page": 1,
        "pages": 3,
        "shortfall": shortfall,
        "incomplete_pages": incomplete_pages,
        "truncated": truncated,
    }


def write(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return path


def test_summary_counts_each_kind_of_problem(module):
    summary = module.summarize(
        [
            entry(600, 600),
            entry(300, 130, shortfall=170),
            entry(40, 40, incomplete_pages=1),
            entry(1500, 1000, truncated=True),
        ]
    )

    assert summary["periods"] == 4
    assert summary["collected"] == 1770
    assert summary["announced"] == 600 + 300 + 40 + 1000
    assert (summary["with_shortfall"], summary["shortfall_total"]) == (1, 170)
    assert (summary["incomplete"], summary["truncated"]) == (1, 1)
    assert len(summary["flagged"]) == 3


def test_clean_audit_exits_with_zero(module, tmp_path, capsys):
    path = write(tmp_path / "audit.jsonl", [entry(600, 600), entry(250, 250)])

    assert module.main(["--audit-file", str(path)]) == 0

    assert "Keine auffälligen Perioden" in capsys.readouterr().out


def test_flagged_period_is_listed_and_exits_with_one(module, tmp_path, capsys):
    path = write(tmp_path / "audit.jsonl", [entry(600, 600), entry(300, 130, shortfall=170)])

    assert module.main(["--audit-file", str(path)]) == 1

    out = capsys.readouterr().out
    assert "Fehlbetrag 170" in out
    assert "Stars 1..5" in out


def test_missing_file_exits_with_two(module, tmp_path, capsys):
    assert module.main(["--audit-file", str(tmp_path / "absent.jsonl")]) == 2

    assert "Kein Prüfprotokoll" in capsys.readouterr().err
