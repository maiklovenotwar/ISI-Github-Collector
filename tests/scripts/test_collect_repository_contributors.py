"""Tests for scripts/collection/collect_repository_contributors.py.

The GitHub API is replaced by a stub; the database is a temporary SQLite file. The script
writes with MySQL-specific SQL, so the write path itself is only checked as compiled SQL.
"""

import importlib.util
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import mysql

from github_collector.database.database import session_scope
from github_collector.database.engine import make_engine
from github_collector.database.models import Contributor, Repository

SCRIPT = (
    Path(__file__).parent.parent.parent
    / "scripts"
    / "collection"
    / "collect_repository_contributors.py"
)
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def sqlite_only_engine(db_url=None):
    """Stand-in for make_engine that can never reach a database server."""
    engine = make_engine(db_url)
    assert engine.dialect.name == "sqlite", f"test tried to open {engine.dialect.name}"
    return engine


def fail_if_called(*args, **kwargs):
    raise AssertionError("must not be called")


@pytest.fixture
def module(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("collect_repo_contributors_under_test", SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    monkeypatch.setattr(loaded, "LOG_FILE", tmp_path / "collect.log")
    monkeypatch.setattr(loaded, "make_engine", sqlite_only_engine)
    return loaded


@pytest.fixture
def engine(test_db):
    with session_scope(test_db.session_factory) as session:
        session.add(Contributor(id=7, login="alice", type="User"))
        session.flush()
        session.add(Repository(id=10, name="repo", full_name="alice/repo", owner_id=7))
    return create_engine(test_db.db_url)


class FakeApi:
    def __init__(self, pages):
        self.pages = pages
        self.calls = 0

    def get_repository_contributors(self, owner, name, per_page, page):
        self.calls += 1
        return self.pages[page - 1] if page <= len(self.pages) else []


def row(login, github_user_id):
    return {
        "repository_id": 10,
        "github_login": login,
        "github_user_id": github_user_id,
        "github_type": "User" if github_user_id else None,
        "contributions": 1,
        "is_anonymous": github_user_id is None,
        "collected_at": NOW,
    }


def test_rows_for_account_and_anonymous_author(module):
    rows = module.contributor_rows(
        10,
        [
            {"login": "alice", "id": 7, "type": "User", "contributions": 3},
            {"email": "a@example.org", "name": "A", "contributions": 1},
            {"name": "B", "contributions": 5},
            {"contributions": 2},
        ],
        NOW,
    )

    assert rows[0] == {
        "repository_id": 10,
        "github_login": "alice",
        "github_user_id": 7,
        "github_type": "User",
        "contributions": 3,
        "is_anonymous": False,
        "collected_at": NOW,
    }
    assert rows[1]["github_login"] == "anon:a@example.org"
    assert (rows[1]["github_user_id"], rows[1]["github_type"]) == (None, None)
    assert rows[1]["is_anonymous"] is True
    assert rows[2]["github_login"] == "anon:B"
    assert rows[3]["github_login"] == "anon:unknown"


def test_login_is_cut_to_column_width(module):
    rows = module.contributor_rows(1, [{"login": "x" * 300, "id": 1}], NOW)

    assert len(rows[0]["github_login"]) == 255


def test_pagination_stops_on_short_page(module):
    api = FakeApi(pages=[[{}] * 100, [{}] * 40, [{}] * 100])

    assert len(module.fetch_all_contributors(api, "o", "r", max_pages=20)) == 140
    assert api.calls == 2


def test_pagination_stops_on_empty_page(module):
    api = FakeApi(pages=[[{}] * 100])

    assert len(module.fetch_all_contributors(api, "o", "r", max_pages=20)) == 100
    assert api.calls == 2


def test_pagination_respects_max_pages(module):
    api = FakeApi(pages=[[{}] * 100] * 5)

    assert len(module.fetch_all_contributors(api, "o", "r", max_pages=3)) == 300
    assert api.calls == 3


def test_flush_resolves_known_contributors_in_dry_run(module, engine):
    buffer = [row("alice", 7), row("bob", 8), row("anon:x", None)]

    assert module.flush_buffer(engine, buffer, "run-1", dry_run=True) == 3

    assert [r["contributor_id"] for r in buffer] == [7, None, None]
    assert {r["run_id"] for r in buffer} == {"run-1"}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM repository_contributors")).scalar() == 0


def test_flush_of_empty_buffer_does_nothing(module, engine):
    assert module.flush_buffer(engine, [], "run-1", dry_run=False) == 0


def test_upsert_is_a_mysql_upsert(module):
    rows = [dict(row("alice", 7), contributor_id=7, run_id="run-1")]

    sql = str(module.upsert_statement(rows).compile(dialect=mysql.dialect()))

    assert sql.startswith("INSERT INTO repository_contributors")
    assert "ON DUPLICATE KEY UPDATE" in sql


def test_refuses_to_write_on_sqlite_before_any_request(module, monkeypatch, engine, capsys):
    monkeypatch.setattr(module.config, "DB_URL", str(engine.url))
    monkeypatch.setattr(module, "GitHubAPI", fail_if_called)
    monkeypatch.setattr(module.SecretManager, "get_github_tokens", staticmethod(fail_if_called))

    assert module.main(["--min-stars", "1"]) == 2

    assert "MySQL" in capsys.readouterr().err


def test_max_stars_must_exceed_min_stars(module):
    with pytest.raises(SystemExit) as exc:
        module.parse_arguments(["--min-stars", "500", "--max-stars", "500"])

    assert exc.value.code == 2


def test_open_ended_star_range_is_accepted(module):
    args = module.parse_arguments(["--min-stars", "500"])

    assert (args.min_stars, args.max_stars, args.max_pages, args.flush_size) == (500, None, 20, 500)


def test_failed_repositories_are_written_to_the_log_directory(module):
    assert module.FAILED_REPOS_DIR == module.config.LOG_DIR


def test_help_runs_as_a_process():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, timeout=120
    )

    assert result.returncode == 0
    assert "--min-stars" in result.stdout


@pytest.fixture
def star_ranges(test_db):
    """Five repositories with different star counts, owners and collection states."""
    from github_collector.database.models import Organization, RepositoryContributor

    with session_scope(test_db.session_factory) as session:
        session.add(Contributor(id=1, login="alice", type="User"))
        session.add(Organization(id=20, login="acme"))
        session.flush()
        session.add_all(
            [
                Repository(
                    id=1, name="r1000", full_name="alice/r1000", owner_id=1, stargazers_count=1000
                ),
                Repository(
                    id=2, name="r800", full_name="alice/r800", owner_id=1, stargazers_count=800
                ),
                Repository(
                    id=3,
                    name="r600",
                    full_name="acme/r600",
                    owner_id=1,
                    organization_id=20,
                    stargazers_count=600,
                ),
                Repository(
                    id=4, name="r500", full_name="alice/r500", owner_id=1, stargazers_count=500
                ),
                Repository(
                    id=5, name="r100", full_name="alice/r100", owner_id=1, stargazers_count=100
                ),
            ]
        )
        session.flush()
        # r800 has been collected before
        session.add(RepositoryContributor(repository_id=2, github_login="alice", collected_at=NOW))
    return create_engine(test_db.db_url)


def _names(repos):
    return [repo["name"] for repo in repos]


def test_selection_covers_the_half_open_star_range_in_descending_order(module, star_ranges):
    repos = module.get_target_repos(star_ranges, 500, 1000, None, False)

    # 1000 is excluded (upper bound exclusive), 800 already has rows, 100 is below the range
    assert _names(repos) == ["r600", "r500"]


def test_selection_without_upper_bound_is_open_ended(module, star_ranges):
    assert _names(module.get_target_repos(star_ranges, 500, None, None, False)) == [
        "r1000",
        "r600",
        "r500",
    ]


def test_force_includes_repositories_that_already_have_rows(module, star_ranges):
    assert _names(module.get_target_repos(star_ranges, 500, 1000, None, True)) == [
        "r800",
        "r600",
        "r500",
    ]


def test_limit_keeps_the_repositories_with_the_most_stars(module, star_ranges):
    assert _names(module.get_target_repos(star_ranges, 500, None, 2, False)) == ["r1000", "r600"]


def test_owner_is_the_organization_when_the_repository_has_one(module, star_ranges):
    owners = {
        r["name"]: r["owner"] for r in module.get_target_repos(star_ranges, 500, 1000, None, False)
    }

    assert owners == {"r600": "acme", "r500": "alice"}


def test_dry_run_requests_the_lists_and_writes_nothing(module, monkeypatch, star_ranges, tmp_path):
    from github_collector.api.github_api import GitHubAPIError

    requested = []

    class StubApi:
        def __init__(self, tokens, cache_dir=None):
            assert tokens == ["token-0"]

        def get_repository_contributors(self, owner, name, per_page, page):
            requested.append(f"{owner}/{name}")
            if name == "r500":
                raise GitHubAPIError("403 contributor list too large")
            return [{"login": "bob", "id": 2, "type": "User", "contributions": 4}]

    monkeypatch.setattr(module.config, "DB_URL", str(star_ranges.url))
    monkeypatch.setattr(module, "GitHubAPI", StubApi)
    monkeypatch.setattr(
        module.SecretManager, "get_github_tokens", staticmethod(lambda: ["token-0"])
    )
    monkeypatch.setattr(module, "FAILED_REPOS_DIR", tmp_path)

    assert module.main(["--min-stars", "500", "--max-stars", "1000", "--dry-run"]) == 0

    assert requested == ["acme/r600", "alice/r500"]
    with star_ranges.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM repository_contributors")).scalar() == 1
    failed_files = list(tmp_path.glob("failed_repos_*.txt"))
    assert len(failed_files) == 1
    assert failed_files[0].read_text(encoding="utf-8") == "alice/r500"


def _rows(*logins, repository_id=7):
    return [
        {"repository_id": repository_id, "github_login": login, "github_type": "User"}
        for login in logins
    ]


@pytest.fixture
def failing_writes(module, monkeypatch):
    """Batch writes always fail; a single row fails if its login is "bad"."""
    monkeypatch.setattr(module, "upsert_statement", lambda rows: rows)

    def write(engine, rows):
        if len(rows) > 1 or rows[0]["github_login"] == "bad":
            raise RuntimeError("Data too long")

    monkeypatch.setattr(module, "_write_batch", write)
    return module


def test_overlong_type_is_shortened_and_logged(module, caplog):
    with caplog.at_level("WARNING"):
        assert len(module._truncate("x" * 30, "github_type")) == 20
    assert "github_type" in caplog.text


def test_failed_batch_is_written_row_by_row(failing_writes):
    assert failing_writes.flush_rows(None, _rows("a", "bad", "c")) == 2


def test_repositories_with_lost_rows_are_reported(failing_writes, caplog):
    # NOT EXISTS selects a repository again only if it has no row at all, so a repository
    # with some rows written stays incomplete (final review of the functional-test plan, C8).
    rows = _rows("a", "bad") + _rows("d", repository_id=8)
    with caplog.at_level("ERROR"):
        failing_writes.flush_rows(None, rows)
    lost = [r.getMessage() for r in caplog.records if "unvollständig" in r.getMessage()]
    assert len(lost) == 1 and "7" in lost[0] and "8" not in lost[0]
