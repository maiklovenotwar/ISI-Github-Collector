"""Tests for the ground-truth loader of the Orbis comparison script.

The reviewed assignments (GitHub login to BvD identifier) are derived from the licensed
Orbis database and are therefore read from a local, untracked file instead of being part
of the script. Loading the script opens no database connection.
"""

import importlib.util
import re
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).parent.parent.parent
    / "workstreams"
    / "orbis_matching"
    / "scripts"
    / "compare_pipeline_with_reviewed_batches.py"
)


@pytest.fixture(scope="module")
def module():
    spec = importlib.util.spec_from_file_location("compare_with_batches_under_test", SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_rows_are_read_in_file_order(module, tmp_path):
    source = tmp_path / "gt.csv"
    source.write_text(
        'login,expected,note\nacme,XX000000001,"hoch, eindeutig"\nlab,NO_MATCH_EXPECTED,Labor\n',
        encoding="utf-8",
    )

    assert module.load_ground_truth(source) == [
        ("acme", "XX000000001", "hoch, eindeutig"),
        ("lab", "NO_MATCH_EXPECTED", "Labor"),
    ]


def test_missing_file_names_the_expected_location(module, tmp_path):
    with pytest.raises(SystemExit) as exc:
        module.load_ground_truth(tmp_path / "absent.csv")

    assert "absent.csv" in str(exc.value)
    assert "login,expected,note" in str(exc.value)


def test_default_file_lies_in_the_untracked_data_folder(module):
    assert module.GROUND_TRUTH_FILE.name == "reviewed_ground_truth.csv"
    assert module.GROUND_TRUTH_FILE.parent == SCRIPT.parent.parent / "data"


def test_script_contains_no_bvd_identifier():
    source = SCRIPT.read_text(encoding="utf-8")

    assert not re.search(r"\b[A-Z]{2}\d{6,}", source)
