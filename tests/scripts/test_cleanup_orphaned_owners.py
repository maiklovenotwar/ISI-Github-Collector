"""The cleanup script reports by default and deletes only with --write."""

import sys

import pytest

from github_collector.cli import cleanup_command
from github_collector.database.database import GitHubDatabase, session_scope
from github_collector.database.models import Contributor, Organization, Repository


@pytest.fixture
def database(tmp_path):
    url = f"sqlite:///{tmp_path / 'cleanup.db'}"
    db = GitHubDatabase(url)
    with session_scope(db.session_factory) as session:
        session.add(Contributor(id=1, login="owner", type="User"))
        session.add_all(
            [Organization(id=20, login="owning-org"), Organization(id=21, login="orphan")]
        )
        session.flush()
        session.add(Repository(id=10, name="a", full_name="owning-org/a", owner_id=20))
    return url


def _organization_logins(url):
    db = GitHubDatabase(url)
    with session_scope(db.session_factory) as session:
        logins = {row.login for row in session.query(Organization.login)}
    return logins


def _run(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["cleanup_orphaned_owners.py", *args])
    return cleanup_command.main()


def test_without_write_nothing_is_deleted(monkeypatch, database):
    assert _run(monkeypatch, "--db-path", database) == 0
    assert _organization_logins(database) == {"owning-org", "orphan"}


def test_with_write_only_the_orphan_is_deleted(monkeypatch, database):
    assert _run(monkeypatch, "--db-path", database, "--write") == 0
    assert _organization_logins(database) == {"owning-org"}
