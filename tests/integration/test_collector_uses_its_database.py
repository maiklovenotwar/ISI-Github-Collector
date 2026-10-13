"""The collector must write to the database it was given, not to the configured default.

Before this was fixed, the batch writer opened its session from the global session factory,
which is bound to the database of the configuration. A collector created for another
database (`--db-path`, or a test database) therefore wrote its repositories into the
configured one.
"""

from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy import create_engine, inspect, text

from github_collector.database import database as db_module
from github_collector.database.database import GitHubDatabase
from github_collector.repository_collector import RepositoryCollector

REPOSITORY = {
    "id": 987001,
    "name": "isolation-probe",
    "full_name": "probe-owner/isolation-probe",
    "owner": {"id": 987002, "login": "probe-owner", "type": "User"},
    "stargazers_count": 5,
    "private": False,
    "fork": False,
    "created_at": "2022-01-15T10:00:00Z",
    "updated_at": "2023-06-01T12:00:00Z",
    "pushed_at": "2023-06-01T11:00:00Z",
}


def _count(engine, table: str) -> int:
    if not inspect(engine).has_table(table):
        return 0
    with engine.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()


def test_batch_is_written_to_the_collectors_database_only(tmp_path: Path) -> None:
    own_url = f"sqlite:///{tmp_path / 'own.db'}"
    default_engine = db_module.SessionLocal().get_bind()
    assert default_engine.dialect.name == "sqlite"  # see tests/conftest.py
    default_before = _count(default_engine, "repositories")

    api = MagicMock()
    api.monitor_rate_limit.return_value = {"has_critical": False}
    api.get_contributor.return_value = {"id": 987002, "login": "probe-owner", "type": "User"}
    api.get_organization.return_value = None
    collector = RepositoryCollector(github_client=api, db=GitHubDatabase(own_url))

    stored = collector._process_repositories_batch([REPOSITORY])

    assert stored == 1
    assert _count(create_engine(own_url), "repositories") == 1
    assert _count(default_engine, "repositories") == default_before
