"""An aborted non-interactive collection reports the abort and nothing else."""

import argparse
import importlib.util
from pathlib import Path

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
