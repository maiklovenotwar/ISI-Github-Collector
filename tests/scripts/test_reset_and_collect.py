"""Regression tests for scripts/maintenance/reset_and_collect.py.

The script deletes the configured database. Invocations that only ask for
usage information, or that pass arguments the script does not know, must
leave the database untouched, and a reset requires the explicit ``--yes`` flag.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "maintenance" / "reset_and_collect.py"
SRC = Path(__file__).parent.parent.parent / "src"


def _run(args, db_file: Path, dry_run: bool = False) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_file.as_posix()}"
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONIOENCODING"] = "utf-8"
    # A dry-run setting inherited from the environment would mask a real deletion.
    env.pop("GITHUB_COLLECTOR_DRY_RUN", None)
    if dry_run:
        env["GITHUB_COLLECTOR_DRY_RUN"] = "true"
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=env,
        cwd=db_file.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


@pytest.mark.parametrize(
    "args,expected_exit",
    [
        (["--help"], 0),
        (["-h"], 0),
        (["--dry-run"], 2),  # not an option of this script; argparse rejects it
        (["unexpected"], 2),
    ],
)
def test_non_reset_invocations_leave_database_untouched(tmp_path, args, expected_exit):
    db_file = tmp_path / "github_data.db"
    content = b"existing database content"
    db_file.write_bytes(content)

    result = _run(args, db_file)

    assert result.returncode == expected_exit, result.stderr
    assert db_file.exists(), "database file was deleted"
    assert db_file.read_bytes() == content, "database file was modified"


def test_help_prints_usage(tmp_path):
    db_file = tmp_path / "github_data.db"
    db_file.write_bytes(b"x")

    result = _run(["--help"], db_file)

    assert "usage:" in result.stdout


def test_invocation_without_yes_leaves_sqlite_database_untouched(tmp_path):
    db_file = tmp_path / "github_data.db"
    content = b"existing database content"
    db_file.write_bytes(content)

    result = _run([], db_file)

    assert result.returncode == 2, result.stderr
    assert "--yes" in result.stderr
    assert db_file.read_bytes() == content, "database file was modified"


@pytest.mark.parametrize("args", [[], ["--yes"]])
def test_dry_run_leaves_database_untouched(tmp_path, args):
    db_file = tmp_path / "github_data.db"
    content = b"existing database content"
    db_file.write_bytes(content)

    result = _run(args, db_file, dry_run=True)

    assert result.returncode == 0, result.stderr
    assert db_file.read_bytes() == content, "database file was modified"


def _load_script(monkeypatch, db_url: str):
    """Import the script in-process with the database URL pinned to ``db_url``.

    The collection step is replaced by a stub, so no GitHub API request is sent.
    """
    spec = importlib.util.spec_from_file_location("reset_and_collect_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    collect_calls = []
    monkeypatch.delenv("GITHUB_COLLECTOR_ALLOW_MYSQL_RESET", raising=False)
    monkeypatch.setattr(module.config, "DB_URL", db_url)
    monkeypatch.setattr(module, "DRY_RUN", False)
    monkeypatch.setattr(module, "run_collect_repositories", lambda: collect_calls.append(1) or True)
    # Guard: the reset below must act on the temporary file and on nothing else.
    assert str(module.config.DB_URL) == db_url
    return module, collect_calls


def test_yes_resets_sqlite_database_and_starts_collection(tmp_path, monkeypatch):
    db_file = tmp_path / "github_data.db"
    db_file.write_bytes(b"existing database content")
    module, collect_calls = _load_script(monkeypatch, f"sqlite:///{db_file.as_posix()}")

    exit_code = module.main(["--yes"])

    assert exit_code == 0
    assert db_file.read_bytes().startswith(b"SQLite format 3"), "schema was not recreated"
    assert collect_calls == [1]


def test_main_without_yes_does_not_reset_or_collect(tmp_path, monkeypatch):
    db_file = tmp_path / "github_data.db"
    content = b"existing database content"
    db_file.write_bytes(content)
    module, collect_calls = _load_script(monkeypatch, f"sqlite:///{db_file.as_posix()}")

    with pytest.raises(SystemExit) as excinfo:
        module.main([])

    assert excinfo.value.code == 2
    assert db_file.read_bytes() == content, "database file was modified"
    assert collect_calls == []
