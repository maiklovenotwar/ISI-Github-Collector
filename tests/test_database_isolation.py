"""The test suite must never reach the database configured in .env."""

from github_collector import config
from github_collector.database import database


def test_configured_database_is_a_temporary_sqlite_file():
    assert str(config.DB_URL).startswith("sqlite:///")
    assert "github_collector_test_db_" in str(config.DB_URL)


def test_global_session_is_bound_to_sqlite():
    session = database.SessionLocal()
    try:
        assert session.get_bind().dialect.name == "sqlite"
    finally:
        session.close()
