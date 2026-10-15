"""The database password must not reach a log (finding K13).

No connection is opened: init_db and GitHubDatabase are replaced in the tests that log.
"""

import argparse
import logging

from github_collector.cli import export_command, geocoding_command
from github_collector.database import database
from github_collector.database.engine import redact_db_url

SECRET = "s3cr3t-pw"  # pragma: allowlist secret
SERVER_URL = (
    f"mysql+mysqlconnector://maik:{SECRET}@db-host:3306/github_data"  # pragma: allowlist secret
)


def test_redact_hides_the_password_and_keeps_host_and_database():
    redacted = redact_db_url(SERVER_URL)

    assert SECRET not in redacted
    assert "db-host:3306/github_data" in redacted
    assert redacted.startswith("mysql+mysqlconnector://maik:")


def test_redact_leaves_a_sqlite_url_unchanged():
    assert redact_db_url("sqlite:///C:/data/github.db") == "sqlite:///C:/data/github.db"


def test_redact_does_not_echo_an_unparsable_value():
    assert SECRET not in redact_db_url(f"garbage {SECRET}")


def test_database_init_does_not_log_the_password(monkeypatch, caplog):
    monkeypatch.setattr(database, "init_db", lambda url: None)
    with caplog.at_level(logging.INFO):
        database.GitHubDatabase(SERVER_URL)

    assert "db-host" in caplog.text
    assert SECRET not in caplog.text


def test_geocoding_command_does_not_log_the_password(monkeypatch, caplog):
    monkeypatch.setattr(geocoding_command, "GitHubDatabase", lambda url: None)
    monkeypatch.setattr(geocoding_command.config, "DB_URL", SERVER_URL)
    with caplog.at_level(logging.INFO):
        geocoding_command._initialize_database(argparse.Namespace(db_path=None))

    assert "db-host" in caplog.text
    assert SECRET not in caplog.text


def test_export_command_does_not_log_the_password(monkeypatch, caplog, tmp_path):
    class StopError(Exception):
        pass

    def stop(url):
        raise StopError

    monkeypatch.setattr(export_command, "GitHubDatabase", stop)
    monkeypatch.setattr(export_command.config, "DB_URL", SERVER_URL)
    monkeypatch.setattr("sys.argv", ["export", "--output-dir", str(tmp_path)])
    with caplog.at_level(logging.INFO):
        try:
            export_command.main()
        except StopError:
            pass

    assert "db-host" in caplog.text
    assert SECRET not in caplog.text
