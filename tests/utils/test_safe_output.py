"""Files with Orbis content may only be written to paths Git ignores (task 6, P-I7)."""

from pathlib import Path

import pytest

from github_collector.utils.safe_output import DEFAULT_ORBIS_OUTPUT_DIR, require_ignored_path

REPO = Path(__file__).resolve().parent.parent.parent


def test_rejects_a_path_git_does_not_ignore():
    # The repository root is not ignored: an export there could be committed by accident.
    with pytest.raises(ValueError, match="not ignored"):
        require_ignored_path(REPO / "orbis_dry_run.csv", REPO)


def test_accepts_the_ignored_orbis_data_folder():
    target = REPO / DEFAULT_ORBIS_OUTPUT_DIR / "dry_run.csv"
    assert require_ignored_path(target, REPO) == target
