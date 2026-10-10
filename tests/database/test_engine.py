"""Tests for github_collector.database.engine.make_engine.

No connection is opened: SQLAlchemy creates engines lazily, so the server URL below is
never contacted.
"""

from github_collector import config
from github_collector.database.engine import make_engine

SERVER_URL = (
    "mysql+mysqlconnector://user:password@localhost/github_data"  # pragma: allowlist secret
)


def test_sqlite_url_gets_plain_engine(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'a.db'}")

    assert engine.dialect.name == "sqlite"


def test_server_url_enables_reconnect_handling():
    engine = make_engine(SERVER_URL)

    assert engine.dialect.name == "mysql"
    assert engine.pool._pre_ping is True
    assert engine.pool._recycle == config.DB_CONFIG["pool_recycle"]
    assert engine.pool._timeout == config.DB_CONFIG["pool_timeout"]


def test_default_url_is_read_at_call_time(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_URL", f"sqlite:///{tmp_path / 'b.db'}")

    assert str(make_engine().url).endswith("b.db")
