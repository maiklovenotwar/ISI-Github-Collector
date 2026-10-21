"""An aborted non-interactive collection reports the abort and nothing else."""

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "collection" / "collect_repositories.py"


def load_script():
    spec = importlib.util.spec_from_file_location("collect_repositories_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AbortingCollector:
    def __init__(self, **kwargs):
        self.state = self

    def get_current_period(self):
        return None

    def collect_repositories(self, **kwargs):
        raise RuntimeError("Data too long for column 'default_branch'")


def test_abort_returns_failure_without_follow_up_errors(monkeypatch, capfd):
    script = load_script()
    monkeypatch.setattr(script, "RepositoryCollector", AbortingCollector)
    args = argparse.Namespace(
        start_date="2026-01-01",
        end_date="2026-01-01",
        time_range=None,
        limit=None,
        star_range=None,
        min_stars=1,
    )

    assert script.non_interactive_mode(args, api_client=None, db=object()) is False
    output = "".join(capfd.readouterr())  # the script's logger does not reach caplog
    assert "Sammlung ABGEBROCHEN" in output
    assert "Rollback" not in output and "Session" not in output


@pytest.mark.parametrize(
    "argv, failing",
    [
        (["--analyze", "--non-interactive"], None),  # no time range given
        (["--stats"], "show_database_stats"),
        (["--non-interactive"], "setup_api_client"),
    ],
)
def test_errors_outside_the_collection_end_with_exit_code_1(monkeypatch, argv, failing):
    # They printed an error and still ended with exit code 0 (functional test 2026-10-20).
    script = load_script()
    monkeypatch.setattr(script, "GitHubDatabase", lambda url: object())
    monkeypatch.setattr(script, "setup_api_client", lambda: object())
    monkeypatch.setattr(script, "RepositoryCollector", AbortingCollector)

    def fail(*args, **kwargs):
        raise ValueError("failed")

    if failing:
        monkeypatch.setattr(script, failing, fail)
    monkeypatch.setattr(sys, "argv", ["collect_repositories.py", *argv])

    assert script.main() == 1
