"""Tests for scripts/collection/collect_contributor_network.py.

The script only starts other scripts as processes. Here the process launchers are replaced,
so nothing is started, no database is opened and no request is sent.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).parent.parent.parent
    / "scripts"
    / "collection"
    / "collect_contributor_network.py"
)


def fail_if_called(*args, **kwargs):
    raise AssertionError("must not be called")


@pytest.fixture
def module(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("collect_contributor_network_under_test", SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    monkeypatch.setattr(loaded, "LOG_FILE", tmp_path / "network.log")
    return loaded


@pytest.fixture
def launched(module, monkeypatch):
    """Records the commands the script would start; every one 'succeeds'."""
    commands = []

    def run(cmd, log_file=None):
        commands.append(cmd)
        return 0

    def run_parallel(cmds, log_files):
        commands.extend(cmds)
        return [0] * len(cmds)

    monkeypatch.setattr(module, "run", run)
    monkeypatch.setattr(module, "run_parallel", run_parallel)
    monkeypatch.setattr(
        module.SecretManager, "get_github_tokens", staticmethod(lambda: ["token-0", "token-1"])
    )
    return commands


def test_cohort_labels(module):
    assert module.cohort_label(500, 1000) == "500to1000stars"
    assert module.cohort_label(500, None) == "500stars"


def test_every_step_script_exists(module):
    assert set(module.STEP_SCRIPTS) == {"collect", "add_missing", "enrich", "geocode"}
    assert all(path.is_file() for path in module.STEP_SCRIPTS.values())


def test_one_enrichment_process_per_token(module):
    cmds = module.enrich_commands("py", 3)

    assert len(cmds) == 3
    assert cmds[2][-6:] == ["--token-index", "2", "--shard-index", "2", "--shard-count", "3"]
    assert all(c[1].endswith("enrich_contributor_profiles.py") for c in cmds)
    assert all(c[2:4] == ["--scope", "new"] for c in cmds)


def test_logs_go_to_the_log_directory(module):
    log_dir = module.config.LOG_DIR

    assert module.log_path("collect", "500stars") == log_dir / "collect_500stars.log"
    assert module.log_path("add_missing", "500stars") == log_dir / "add_missing_500stars.log"
    assert module.log_path("geocode", "500stars") == log_dir / "geocoding_500stars.log"
    assert module.log_path("enrich", "500stars", 2).name == "enrich_profiles_500stars_shard2.log"


def test_all_four_steps_run_in_order(module, launched):
    assert module.main(["--min-stars", "500", "--max-stars", "1000"]) == 0

    started = [Path(cmd[1]).name for cmd in launched]
    assert started == [
        "collect_repository_contributors.py",
        "add_missing_contributors.py",
        "enrich_contributor_profiles.py",
        "enrich_contributor_profiles.py",
        "update_location_geocoding.py",
    ]
    assert launched[0][2:] == ["--min-stars", "500", "--max-pages", "20", "--max-stars", "1000"]
    assert launched[-1][2:] == ["--contributors", "--no-interactive-filters", "--yes"]


def test_dry_run_stops_after_the_collection_step(module, launched):
    assert module.main(["--min-stars", "500", "--dry-run"]) == 0

    assert [Path(cmd[1]).name for cmd in launched] == ["collect_repository_contributors.py"]
    assert launched[0][-1] == "--dry-run"


def test_skipped_steps_are_not_started(module, launched):
    assert module.main(["--min-stars", "500", "--skip-collect", "--skip-stubs"]) == 0

    assert [Path(cmd[1]).name for cmd in launched] == [
        "enrich_contributor_profiles.py",
        "enrich_contributor_profiles.py",
        "update_location_geocoding.py",
    ]


def test_a_failing_step_stops_the_pipeline_with_its_exit_code(module, launched, monkeypatch):
    monkeypatch.setattr(module, "run", lambda cmd, log_file=None: launched.append(cmd) or 3)

    assert module.main(["--min-stars", "500"]) == 3

    assert [Path(cmd[1]).name for cmd in launched] == ["collect_repository_contributors.py"]


def test_a_failing_enrichment_shard_stops_the_pipeline(module, launched, monkeypatch):
    monkeypatch.setattr(module, "run_parallel", lambda cmds, log_files: [0, 1])

    assert module.main(["--min-stars", "500", "--skip-collect", "--skip-stubs"]) == 1

    assert launched == []


def test_no_token_is_an_error(module, monkeypatch):
    monkeypatch.setattr(module.SecretManager, "get_github_tokens", staticmethod(lambda: []))
    monkeypatch.setattr(module, "run", fail_if_called)
    monkeypatch.setattr(module, "run_parallel", fail_if_called)

    args = ["--min-stars", "500", "--skip-collect", "--skip-stubs", "--skip-geocode"]
    assert module.main(args) == 1


def test_max_stars_must_exceed_min_stars(module):
    with pytest.raises(SystemExit) as exc:
        module.parse_args(["--min-stars", "500", "--max-stars", "400"])

    assert exc.value.code == 2


def test_help_runs_as_a_process():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, timeout=120
    )

    assert result.returncode == 0
    assert "--min-stars" in result.stdout


def test_dry_run_never_starts_a_writing_step_even_when_collection_is_skipped(module, launched):
    assert module.main(["--min-stars", "500", "--skip-collect", "--dry-run"]) == 0

    assert launched == []


def test_unavailable_token_is_reported_as_an_error(module, monkeypatch):
    def no_token():
        raise ValueError("no token configured")

    monkeypatch.setattr(module.SecretManager, "get_github_tokens", staticmethod(no_token))
    monkeypatch.setattr(module, "run", fail_if_called)
    monkeypatch.setattr(module, "run_parallel", fail_if_called)

    args = ["--min-stars", "500", "--skip-collect", "--skip-stubs", "--skip-geocode"]
    assert module.main(args) == 1
