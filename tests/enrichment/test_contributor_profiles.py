"""Tests for the GraphQL profile enrichment of contributors.

All GitHub responses are simulated; the database is a temporary SQLite file.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine

from github_collector.database.database import session_scope
from github_collector.database.models import Contributor, Repository, RepositoryContributor
from github_collector.enrichment import contributor_profiles as profiles

OWNER, COLLABORATOR, CHECKED = 1, 2, 3


def _profile(login: str, **overrides) -> dict:
    data = {
        "login": login,
        "name": f"Name of {login}",
        "email": f"{login}@example.org",
        "websiteUrl": f"https://{login}.example.org",
        "location": "Karlsruhe, Germany",
        "bio": "bio",
        "company": "Example GmbH",
        "twitterUsername": login,
        "createdAt": "2015-03-01T10:00:00Z",
        "updatedAt": "2026-01-15T08:30:00Z",
        "repositories": {"totalCount": 7},
        "gists": {"totalCount": 1},
        "followers": {"totalCount": 12},
        "following": {"totalCount": 3},
    }
    data.update(overrides)
    return data


def _response(body: dict, status_code: int = 200, headers: dict = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.headers = headers or {}
    response.json.return_value = body
    return response


def _session(*bodies) -> MagicMock:
    session = MagicMock()
    session.post.side_effect = [_response(body) for body in bodies]
    return session


@pytest.fixture
def engine(test_db):
    """SQLite database with an owner, a collaborator and an already checked contributor."""
    with session_scope(test_db.session_factory) as session:
        session.add_all(
            [
                Contributor(id=OWNER, login="owner", type="User"),
                Contributor(id=COLLABORATOR, login="collaborator", type="User"),
                Contributor(
                    id=CHECKED,
                    login="checked",
                    type="User",
                    enrichment_checked_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
                ),
            ]
        )
        session.flush()
        session.add(Repository(id=10, name="repo", full_name="owner/repo", owner_id=OWNER))
        session.flush()
        session.add_all(
            [
                RepositoryContributor(
                    repository_id=10,
                    github_login="collaborator",
                    github_user_id=COLLABORATOR,
                    collected_at=datetime.now(timezone.utc),
                ),
                RepositoryContributor(
                    repository_id=10,
                    github_login="anonymous@example.org",
                    is_anonymous=True,
                    collected_at=datetime.now(timezone.utc),
                ),
            ]
        )
    engine = create_engine(test_db.db_url)
    yield engine
    engine.dispose()


def _contributor(test_db, contributor_id: int) -> Contributor:
    with session_scope(test_db.session_factory) as session:
        contributor = session.get(Contributor, contributor_id)
        session.expunge(contributor)
        return contributor


class TestGetCandidates:
    def test_scope_all_selects_every_unchecked_contributor(self, engine):
        candidates = profiles.get_candidates(engine, "all")

        assert {c["id"] for c in candidates} == {OWNER, COLLABORATOR}

    def test_scope_new_selects_only_repository_collaborators(self, engine):
        candidates = profiles.get_candidates(engine, "new")

        assert candidates == [{"id": COLLABORATOR, "login": "collaborator"}]

    def test_scope_gap_selects_only_the_others(self, engine):
        candidates = profiles.get_candidates(engine, "gap")

        assert candidates == [{"id": OWNER, "login": "owner"}]

    def test_unknown_scope_is_rejected(self, engine):
        with pytest.raises(ValueError):
            profiles.get_candidates(engine, "everything")

    def test_shards_are_disjoint_and_complete(self, engine):
        shards = [profiles.get_candidates(engine, "all", i, 2) for i in range(2)]
        ids = [c["id"] for shard in shards for c in shard]

        assert sorted(ids) == [OWNER, COLLABORATOR]
        assert all(c["id"] % 2 == i for i, shard in enumerate(shards) for c in shard)


class TestBuildQuery:
    def test_logins_are_passed_as_variables_not_interpolated(self):
        payload = profiles.build_query(["alice", 'bob") { id } #'])

        assert payload["variables"] == {"login0": "alice", "login1": 'bob") { id } #'}
        assert 'bob")' not in payload["query"]
        assert "user0: user(login: $login0)" in payload["query"]
        assert "user1: user(login: $login1)" in payload["query"]


class TestParseUserData:
    def test_maps_graphql_fields_to_columns(self):
        row = profiles.parse_user_data(_profile("alice"), {"id": 5, "login": "alice"})

        assert row["id"] == 5
        assert row["blog"] == "https://alice.example.org"
        assert row["twitter_username"] == "alice"
        assert row["public_repos"] == 7
        assert row["public_gists"] == 1
        assert row["followers"] == 12
        assert row["following"] == 3
        assert row["created_at"].year == 2015
        assert row["enrichment_checked_at"] is not None

    def test_missing_account_yields_no_row(self):
        assert profiles.parse_user_data(None, {"id": 5, "login": "gone"}) is None

    def test_overlong_values_are_truncated_to_the_column_length(self):
        row = profiles.parse_user_data(
            _profile("alice", twitterUsername="t" * 80, location="l" * 400),
            {"id": 5, "login": "alice"},
        )

        assert len(row["twitter_username"]) == 50
        assert len(row["location"]) == 255

    def test_renamed_account_takes_the_login_reported_by_github(self):
        row = profiles.parse_user_data(_profile("new-name"), {"id": 5, "login": "old-name"})

        assert row["login"] == "new-name"

    def test_unparseable_timestamp_becomes_null(self):
        row = profiles.parse_user_data(
            _profile("alice", createdAt="not a date"), {"id": 5, "login": "alice"}
        )

        assert row["created_at"] is None


class TestFetchBatch:
    def test_returns_data_and_errors(self):
        errors = [{"type": "NOT_FOUND", "path": ["user1"]}]
        session = _session({"data": {"user0": _profile("a"), "user1": None}, "errors": errors})

        data, returned_errors = profiles.fetch_batch(session, "token", ["a", "b"])

        assert data["user0"]["login"] == "a"
        assert data["user1"] is None
        assert returned_errors == errors

    def test_response_without_data_raises(self):
        session = _session({"data": None, "errors": [{"type": "RATE_LIMITED"}]})

        with pytest.raises(profiles.GraphQLResponseError):
            profiles.fetch_batch(session, "token", ["a"])

    def test_token_is_sent_as_bearer_header(self):
        session = _session({"data": {"user0": _profile("a")}})

        profiles.fetch_batch(session, "secret-token", ["a"])

        assert session.post.call_args.kwargs["headers"] == {"Authorization": "Bearer secret-token"}


class TestProcessBatch:
    BATCH = [{"id": OWNER, "login": "owner"}, {"id": COLLABORATOR, "login": "collaborator"}]

    def test_found_profile_is_written_and_marked(self, engine, test_db):
        session = _session(
            {"data": {"user0": _profile("owner"), "user1": _profile("collaborator")}}
        )
        buffer = []

        not_found, parsed = profiles.process_batch(session, "t", engine, self.BATCH, buffer, False)
        written = profiles.flush_rows(engine, buffer)

        assert (not_found, parsed, written) == (0, 2, 2)
        owner = _contributor(test_db, OWNER)
        assert owner.location == "Karlsruhe, Germany"
        assert owner.company == "Example GmbH"
        assert owner.followers == 12
        assert owner.enrichment_checked_at is not None
        assert owner.type == "User", "columns not delivered by the profile query must survive"

    def test_missing_account_is_marked_without_touching_other_columns(self, engine, test_db):
        with session_scope(test_db.session_factory) as session:
            session.get(Contributor, COLLABORATOR).location = "kept"
        session = _session(
            {
                "data": {"user0": _profile("owner"), "user1": None},
                "errors": [{"type": "NOT_FOUND", "path": ["user1"]}],
            }
        )
        buffer = []

        not_found, parsed = profiles.process_batch(session, "t", engine, self.BATCH, buffer, False)

        assert (not_found, parsed) == (1, 1)
        collaborator = _contributor(test_db, COLLABORATOR)
        assert collaborator.enrichment_checked_at is not None
        assert collaborator.location == "kept"

    def test_failed_request_marks_nobody(self, engine, test_db):
        session = MagicMock()
        session.post.return_value = _response({"data": None, "errors": [{"type": "RATE_LIMITED"}]})
        buffer = []

        not_found, parsed = profiles.process_batch(session, "t", engine, self.BATCH, buffer, False)

        assert (not_found, parsed, buffer) == (0, 0, [])
        assert _contributor(test_db, OWNER).enrichment_checked_at is None
        assert _contributor(test_db, COLLABORATOR).enrichment_checked_at is None

    def test_null_with_an_unexpected_error_is_not_marked_as_missing(self, engine, test_db):
        session = _session(
            {
                "data": {"user0": _profile("owner"), "user1": None},
                "errors": [{"type": "SERVICE_UNAVAILABLE", "path": ["user1"]}],
            }
        )
        buffer = []

        not_found, parsed = profiles.process_batch(session, "t", engine, self.BATCH, buffer, False)

        assert (not_found, parsed) == (0, 1)
        assert _contributor(test_db, COLLABORATOR).enrichment_checked_at is None

    def test_dry_run_writes_nothing(self, engine, test_db):
        session = _session({"data": {"user0": _profile("owner"), "user1": None}})
        buffer = []

        not_found, parsed = profiles.process_batch(session, "t", engine, self.BATCH, buffer, True)

        assert (not_found, parsed) == (1, 1)
        assert _contributor(test_db, OWNER).enrichment_checked_at is None
        assert _contributor(test_db, COLLABORATOR).enrichment_checked_at is None

    def test_checked_contributors_are_not_selected_again(self, engine):
        session = _session({"data": {"user0": _profile("owner"), "user1": None}})
        buffer = []
        profiles.process_batch(session, "t", engine, self.BATCH, buffer, False)
        profiles.flush_rows(engine, buffer)

        assert profiles.get_candidates(engine, "all") == []


class TestFlushRows:
    def test_more_rows_than_one_sqlite_statement_holds(self, engine, test_db):
        with session_scope(test_db.session_factory) as session:
            session.add_all(
                Contributor(id=100 + i, login=f"user{i}", type="User") for i in range(120)
            )
        rows = [
            profiles.parse_user_data(_profile(f"user{i}"), {"id": 100 + i, "login": f"user{i}"})
            for i in range(120)
        ]

        assert profiles.flush_rows(engine, rows) == 120
        assert profiles.get_candidates(engine, "gap") == [{"id": OWNER, "login": "owner"}]

    def test_one_unwritable_row_does_not_discard_the_others(self, engine, test_db):
        # The collaborator was renamed to a login that another row still holds.
        rows = [
            profiles.parse_user_data(_profile("owner"), {"id": OWNER, "login": "owner"}),
            profiles.parse_user_data(_profile("checked"), {"id": COLLABORATOR, "login": "x"}),
        ]

        written = profiles.flush_rows(engine, rows)

        assert written == 1
        assert _contributor(test_db, OWNER).enrichment_checked_at is not None
        collaborator = _contributor(test_db, COLLABORATOR)
        assert collaborator.login == "collaborator"
        assert collaborator.enrichment_checked_at is None

    def test_empty_input_is_a_no_op(self, engine):
        assert profiles.flush_rows(engine, []) == 0
