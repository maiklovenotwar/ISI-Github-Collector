"""Tests for scripts/enrichment/enrich_contributor_profiles.py (command line and end-to-end run).

The GitHub API is replaced by a stub session; the database is a temporary SQLite file.
"""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine

from github_collector.database.database import session_scope
from github_collector.database.models import Contributor
from github_collector.enrichment import contributor_profiles as profiles

SCRIPT = (
    Path(__file__).parent.parent.parent
    / "scripts"
    / "enrichment"
    / "enrich_contributor_profiles.py"
)


@pytest.fixture
def script(tmp_path, monkeypatch, test_db):
    spec = importlib.util.spec_from_file_location("enrich_contributor_profiles_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setattr(module, "LOG_FILE", tmp_path / "enrich.log")
    monkeypatch.setattr(module, "make_engine", lambda: create_engine(test_db.db_url))
    monkeypatch.setattr(
        module.SecretManager, "get_github_tokens", staticmethod(lambda: ["token-0", "token-1"])
    )
    return module


def _stub_github(monkeypatch, script, body: dict) -> MagicMock:
    response = MagicMock(status_code=200, headers={})
    response.json.return_value = body
    session = MagicMock()
    session.post.return_value = response
    monkeypatch.setattr(script.requests, "Session", lambda: session)
    return session


def _add_contributors(test_db, *logins):
    with session_scope(test_db.session_factory) as session:
        session.add_all(
            Contributor(id=i, login=login, type="User") for i, login in enumerate(logins, start=1)
        )


def _checked_logins(test_db) -> set:
    with session_scope(test_db.session_factory) as session:
        rows = session.query(Contributor).filter(Contributor.enrichment_checked_at.isnot(None))
        return {row.login for row in rows}


def test_run_writes_profiles_and_marks_missing_accounts(script, test_db, monkeypatch):
    _add_contributors(test_db, "alice", "gone")
    _stub_github(
        monkeypatch,
        script,
        {
            "data": {"user0": {"login": "alice", "location": "Berlin"}, "user1": None},
            "errors": [{"type": "NOT_FOUND", "path": ["user1"]}],
        },
    )

    assert script.main([]) == 0

    assert _checked_logins(test_db) == {"alice", "gone"}
    with session_scope(test_db.session_factory) as session:
        assert session.get(Contributor, 1).location == "Berlin"


def test_dry_run_leaves_the_database_unchanged(script, test_db, monkeypatch):
    _add_contributors(test_db, "alice")
    session = _stub_github(monkeypatch, script, {"data": {"user0": {"login": "alice"}}})

    assert script.main(["--dry-run"]) == 0

    assert session.post.called
    assert _checked_logins(test_db) == set()


def test_second_run_sends_no_request(script, test_db, monkeypatch):
    _add_contributors(test_db, "alice")
    session = _stub_github(monkeypatch, script, {"data": {"user0": {"login": "alice"}}})
    script.main([])
    session.post.reset_mock()

    assert script.main([]) == 0

    session.post.assert_not_called()


def test_selected_token_is_used(script, test_db, monkeypatch):
    _add_contributors(test_db, "alice")
    session = _stub_github(monkeypatch, script, {"data": {"user0": {"login": "alice"}}})

    script.main(["--token-index", "1"])

    assert session.post.call_args.kwargs["headers"] == {"Authorization": "Bearer token-1"}


def test_token_index_beyond_the_configured_tokens_fails(script, test_db, monkeypatch):
    _add_contributors(test_db, "alice")
    session = _stub_github(monkeypatch, script, {"data": {}})

    assert script.main(["--token-index", "5"]) == 1

    session.post.assert_not_called()


@pytest.mark.parametrize(
    "argv",
    [["--shard-index", "2", "--shard-count", "2"], ["--shard-count", "0"], ["--scope", "owners"]],
)
def test_invalid_arguments_are_rejected(script, argv):
    with pytest.raises(SystemExit) as excinfo:
        script.parse_arguments(argv)

    assert excinfo.value.code == 2


def test_mysql_upsert_updates_on_the_primary_key():
    """The production database is MySQL; the statement is compiled, not executed."""
    engine = create_engine("mysql+mysqlconnector://user:password@localhost/github_data")
    rows = [profiles.parse_user_data({"login": "alice"}, {"id": 1, "login": "alice"})]

    sql = str(profiles._upsert_statement(engine, rows).compile(dialect=engine.dialect))

    assert sql.startswith("INSERT INTO contributors")
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "enrichment_checked_at = VALUES(enrichment_checked_at)" in sql
    assert "id = VALUES(id)" not in sql
