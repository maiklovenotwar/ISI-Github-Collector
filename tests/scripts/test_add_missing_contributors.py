"""Tests for scripts/collection/add_missing_contributors.py.

The database is a temporary SQLite file. The script writes with MySQL-specific SQL, so on
SQLite only the selection, the dry run and the refusal to write can be exercised.
"""

import importlib.util
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from github_collector.database.database import session_scope
from github_collector.database.engine import make_engine
from github_collector.database.models import Contributor, Repository, RepositoryContributor

SCRIPT = (
    Path(__file__).parent.parent.parent / "scripts" / "collection" / "add_missing_contributors.py"
)
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def sqlite_only_engine(db_url=None):
    """Stand-in for make_engine that can never reach a database server."""
    engine = make_engine(db_url)
    assert engine.dialect.name == "sqlite", f"test tried to open {engine.dialect.name}"
    return engine


@pytest.fixture
def module(monkeypatch, test_db):
    spec = importlib.util.spec_from_file_location("add_missing_contributors_under_test", SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    monkeypatch.setattr(loaded, "make_engine", sqlite_only_engine)
    monkeypatch.setattr(loaded.config, "DB_URL", test_db.db_url)
    return loaded


def _link(repository_id, login, **fields):
    return RepositoryContributor(
        repository_id=repository_id, github_login=login, collected_at=NOW, **fields
    )


@pytest.fixture
def network(test_db):
    """One known account, one unknown user seen in two repositories, a bot and an anonymous author."""
    with session_scope(test_db.session_factory) as session:
        session.add(Contributor(id=1, login="alice", type="User"))
        session.flush()
        session.add_all(
            [
                Repository(id=10, name="a", full_name="alice/a", owner_id=1),
                Repository(id=11, name="b", full_name="alice/b", owner_id=1),
            ]
        )
        session.flush()
        session.add_all(
            [
                _link(10, "alice", github_user_id=1, github_type="User"),
                _link(10, "bob", github_user_id=2, github_type="User"),
                _link(10, "ci-bot", github_user_id=3, github_type="Bot"),
                _link(10, "anon:x@example.org", is_anonymous=True),
                _link(11, "bob", github_user_id=2, github_type="User"),
            ]
        )
    return test_db


def _contributor_ids(db):
    with session_scope(db.session_factory) as session:
        return {row.id for row in session.query(Contributor.id)}


def test_only_unknown_named_non_bot_accounts_are_selected(module, network):
    missing = module.find_missing_contributors(sqlite_only_engine(network.db_url))

    assert [tuple(row) for row in missing] == [(2, "bob", "User")]


def test_dry_run_reports_the_number_and_changes_nothing(module, network, capsys):
    assert module.main(["--dry-run"]) == 0

    assert "1 " in capsys.readouterr().out
    assert _contributor_ids(network) == {1}


def test_refuses_to_write_on_sqlite(module, network, capsys):
    assert module.main([]) == 2

    assert "MySQL" in capsys.readouterr().err
    assert _contributor_ids(network) == {1}


def test_nothing_to_do_returns_zero_on_any_database(module, test_db):
    assert module.main([]) == 0


def test_help_runs_as_a_process():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, timeout=120
    )

    assert result.returncode == 0
    assert "--dry-run" in result.stdout
