"""Repository statistics enrichment (K4).

contributors_count is not collected and must stay NULL instead of 0; results are written per
batch so that an aborted run keeps them; a database error is not reported as one update; a
leftover checkpoint file must not skip a batch. No request reaches GitHub.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from github_collector.database.database import session_scope
from github_collector.database.engine import make_engine
from github_collector.database.models import Contributor, Repository
from github_collector.enrichment import graphql_handler
from github_collector.enrichment.updater import map_and_update_stats

SCRIPT = (
    Path(__file__).parent.parent.parent / "scripts" / "enrichment" / "enrich_repository_stats.py"
)


@pytest.fixture
def repos(test_db):
    with session_scope(test_db.session_factory) as session:
        session.add(Contributor(id=1, login="owner", type="User"))
        session.flush()
        for repo_id in (101, 102, 103):
            session.add(
                Repository(
                    id=repo_id, name=f"r{repo_id}", full_name=f"owner/r{repo_id}", owner_id=1
                )
            )
    return test_db


def _stats(test_db):
    engine = make_engine(test_db.db_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, contributors_count, commits_count, pull_requests_count "
                "FROM repositories ORDER BY id"
            )
        )
        return {r[0]: tuple(r[1:]) for r in rows}


def _stat(repo_id, commits=5, prs=2):
    return {
        "repo_id": f"node{repo_id}",
        "database_id": repo_id,
        "calculated_commit_count": commits,
        "calculated_pr_count": prs,
        "calculated_contributor_count": None,
    }


def test_update_leaves_contributor_count_null(repos):
    engine = make_engine(repos.db_url)
    assert map_and_update_stats(engine, [_stat(101)]) == 1
    assert _stats(repos)[101] == (None, 5, 2)


def test_database_error_raises_instead_of_counting_one(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    with pytest.raises(OperationalError):
        map_and_update_stats(engine, [_stat(101)])


@pytest.fixture
def script(monkeypatch, repos):
    spec = importlib.util.spec_from_file_location("enrich_repo_stats_under_test", SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    monkeypatch.setattr(loaded.config, "DB_URL", repos.db_url)
    return loaded


def test_repository_with_counts_is_not_selected_again(script, repos):
    engine = make_engine(repos.db_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE repositories SET commits_count = 1, pull_requests_count = 0 WHERE id = 101"
            )
        )
    selected = {r["id"] for r in script.get_repos_to_enrich(repos.db_url)}
    assert selected == {102, 103}


def _fake_handler(behaviour):
    class FakeHandler:
        def __init__(self, batch_size=None):
            self.calls = 0

        def fetch_repo_stats(self, batch):
            self.calls += 1
            return behaviour(self.calls, batch)

    return FakeHandler


def _run(script, monkeypatch, tmp_path, behaviour):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(script, "GraphQLHandler", _fake_handler(behaviour))
    monkeypatch.setattr(sys, "argv", ["enrich_repository_stats.py", "--batch-size", "1"])
    return script.main()


def test_aborted_run_keeps_the_first_batch(script, repos, monkeypatch, tmp_path):
    def behaviour(call, batch):
        if call == 2:
            raise KeyboardInterrupt
        return [_stat(r["id"]) for r in batch], []

    with pytest.raises(KeyboardInterrupt):
        _run(script, monkeypatch, tmp_path, behaviour)
    stats = _stats(repos)
    assert stats[101] == (None, 5, 2)
    assert stats[102] == (None, None, None)


def test_lost_batch_ends_with_exit_code_1(script, repos, monkeypatch, tmp_path):
    def behaviour(call, batch):
        if call == 2:
            raise graphql_handler.GraphQLBatchError("GitHub down")
        return [_stat(r["id"]) for r in batch], []

    assert _run(script, monkeypatch, tmp_path, behaviour) == 1
    assert _stats(repos)[103] == (None, 5, 2)


def test_repository_unknown_to_github_does_not_fail_the_run(script, repos, monkeypatch, tmp_path):
    def behaviour(call, batch):
        if batch[0]["id"] == 102:  # deleted or renamed: GitHub answers null
            return [], [batch]
        return [_stat(r["id"]) for r in batch], []

    assert _run(script, monkeypatch, tmp_path, behaviour) == 0
    assert (tmp_path / next(p.name for p in tmp_path.glob("failed_repo_ids_*.txt"))).exists()


def test_handler_raises_when_every_attempt_fails(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise graphql_handler.requests.exceptions.ConnectionError("offline")

    monkeypatch.setattr(graphql_handler.requests.Session, "post", broken)
    monkeypatch.setattr(graphql_handler.time, "sleep", lambda seconds: None)
    handler = graphql_handler.GraphQLHandler(github_token="x", batch_size=1)
    with pytest.raises(graphql_handler.GraphQLBatchError):
        handler.fetch_repo_stats([{"id": 101, "owner": "o", "name": "n"}])


def test_leftover_checkpoint_does_not_skip_a_batch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "enrich_checkpoint.txt").write_text("1")
    response = MagicMock(status_code=200, headers={})
    response.json.return_value = {
        "data": {
            "repo0": {
                "id": "node1",
                "databaseId": 101,
                "pullRequests": {"totalCount": 3},
                "defaultBranchRef": {"target": {"history": {"totalCount": 9}}},
            }
        }
    }
    monkeypatch.setattr(graphql_handler.requests.Session, "post", lambda *a, **k: response)
    handler = graphql_handler.GraphQLHandler(github_token="x", batch_size=1)
    results, failed = handler.fetch_repo_stats([{"id": 101, "owner": "o", "name": "n"}])
    assert [r["database_id"] for r in results] == [101]
    assert results[0]["calculated_contributor_count"] is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["enrich_checkpoint.txt"]
