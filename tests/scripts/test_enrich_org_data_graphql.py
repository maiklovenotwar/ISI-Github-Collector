"""Tests for the write path of scripts/enrichment/enrich_org_data_graphql.py.

The script records each profile fetch in ``organizations.enrichment_checked_at`` and must
leave the ``orbis_*`` columns, which belong to the Orbis matching pipeline, untouched.
The GitHub API is replaced by a stub; the database is a temporary SQLite file.
"""

import importlib.util
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from github_collector.database.database import session_scope
from github_collector.database.models import Organization

SCRIPT = (
    Path(__file__).parent.parent.parent / "scripts" / "enrichment" / "enrich_org_data_graphql.py"
)
ORBIS_TIMESTAMP = datetime(2026, 8, 7, 12, 0, 0)


@pytest.fixture
def enricher(tmp_path, monkeypatch, test_db):
    spec = importlib.util.spec_from_file_location("enrich_org_data_graphql_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "DB_URL", test_db.db_url)

    enricher = module.GraphQLOrganizationEnricher(
        token="token", cache_file=str(tmp_path / "org_cache.json")
    )
    assert str(enricher.engine.url) == test_db.db_url
    yield enricher
    enricher.engine.dispose()


def _stub_github(enricher, body=None, error: Exception = None) -> MagicMock:
    post = MagicMock()
    if error is not None:
        post.side_effect = error
    else:
        response = MagicMock(status_code=200)
        response.json.return_value = body
        post.return_value = response
    enricher.client.session.post = post
    return post


def _add(test_db, **fields) -> None:
    with session_scope(test_db.session_factory) as session:
        session.add(Organization(**fields))


def _org(test_db, login: str) -> Organization:
    with session_scope(test_db.session_factory) as session:
        organization = session.query(Organization).filter_by(login=login).one()
        session.expunge(organization)
        return organization


def _profile(login: str) -> dict:
    return {
        "login": login,
        "name": "Example Org",
        "email": "info@example.org",
        "websiteUrl": "https://example.org",
        "location": "Karlsruhe",
        "description": "An organization",
        "createdAt": "2015-03-01T10:00:00Z",
        "updatedAt": "2026-01-15T08:30:00Z",
        "isVerified": False,
        "twitterUsername": None,
    }


class TestSelection:
    def test_only_empty_unchecked_organizations_are_selected(self, enricher, test_db):
        _add(test_db, id=1, login="empty")
        _add(test_db, id=2, login="has-name", name="Named")
        _add(test_db, id=3, login="checked", enrichment_checked_at=datetime(2026, 2, 20))

        assert enricher.get_empty_organizations() == ["empty"]


class TestWritePath:
    def test_profile_is_written_and_fetch_is_recorded(self, enricher, test_db):
        _add(test_db, id=1, login="example")
        _stub_github(enricher, {"data": {"org0": _profile("example")}})

        result = enricher.enrich_organizations()

        assert result["stats"]["updated"] == 1
        organization = _org(test_db, "example")
        assert organization.name == "Example Org"
        assert organization.blog == "https://example.org"
        assert organization.location == "Karlsruhe"
        assert organization.bio == "An organization"
        assert organization.created_at.year == 2015
        assert organization.enrichment_checked_at is not None

    def test_orbis_columns_are_not_touched(self, enricher, test_db):
        _add(test_db, id=1, login="example")
        _stub_github(enricher, {"data": {"org0": _profile("example")}})

        enricher.enrich_organizations()

        organization = _org(test_db, "example")
        assert organization.orbis_enriched_at is None
        assert organization.orbis_run_id is None

    def test_existing_orbis_values_survive(self, enricher, test_db):
        _add(
            test_db,
            id=1,
            login="example",
            bvd_id="DE123",
            orbis_enriched_at=ORBIS_TIMESTAMP,
            orbis_run_id="orbis-run",
        )

        assert enricher.update_organization_data("example", {"name": "Example Org"})

        organization = _org(test_db, "example")
        assert organization.orbis_enriched_at == ORBIS_TIMESTAMP
        assert organization.orbis_run_id == "orbis-run"
        assert organization.bvd_id == "DE123"

    def test_existing_profile_values_are_not_overwritten(self, enricher, test_db):
        _add(test_db, id=1, login="example", name="Kept Name")

        enricher.update_organization_data("example", {"name": "New Name", "location": "Berlin"})

        organization = _org(test_db, "example")
        assert organization.name == "Kept Name"
        assert organization.location == "Berlin"


class TestOrganizationsUnknownToGitHub:
    BODY = {
        "data": {"org0": None},
        "errors": [{"type": "NOT_FOUND", "path": ["org0"]}],
    }

    def test_missing_organization_is_recorded_as_checked_only(self, enricher, test_db):
        _add(test_db, id=1, login="gone")
        _stub_github(enricher, self.BODY)

        result = enricher.enrich_organizations()

        assert result["stats"]["not_found"] == 1
        organization = _org(test_db, "gone")
        assert organization.enrichment_checked_at is not None
        assert organization.name is None
        assert organization.orbis_enriched_at is None

    def test_missing_organization_is_not_requested_again(self, enricher, test_db):
        _add(test_db, id=1, login="gone")
        post = _stub_github(enricher, self.BODY)
        enricher.enrich_organizations()
        post.reset_mock()

        enricher.enrich_organizations()

        post.assert_not_called()


class TestFailedRequests:
    @pytest.mark.parametrize(
        "stub",
        [
            {"error": ConnectionError("network down")},
            {"body": {"data": None, "errors": [{"type": "RATE_LIMITED"}]}},
            {"body": {"data": {"org0": None}, "errors": [{"type": "TIMEOUT", "path": ["org0"]}]}},
        ],
        ids=["request-error", "no-data", "unexpected-error-for-alias"],
    )
    def test_nothing_is_recorded_when_the_answer_is_unusable(self, enricher, test_db, stub):
        _add(test_db, id=1, login="example")
        _stub_github(enricher, **stub)

        result = enricher.enrich_organizations()

        assert result["stats"]["not_found"] == 0
        assert result["stats"]["errors"] == 1
        assert _org(test_db, "example").enrichment_checked_at is None
        assert enricher.get_empty_organizations() == ["example"]


def test_dry_run_sends_no_request_and_writes_nothing(enricher, test_db):
    _add(test_db, id=1, login="example")
    post = _stub_github(enricher, {"data": {"org0": _profile("example")}})

    result = enricher.enrich_organizations(dry_run=True)

    assert result == {"dry_run": True, "target_count": 1}
    post.assert_not_called()
    assert _org(test_db, "example").enrichment_checked_at is None


def test_cache_and_log_are_not_written_to_the_working_directory(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    spec = importlib.util.spec_from_file_location("enrich_org_paths_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.config, "CACHE_DIR", tmp_path / "cache")

    cache = module.OrganizationCache()
    cache.save_cache()

    assert cache.cache_file == tmp_path / "cache" / "org_cache.json"
    assert cache.cache_file.exists()
    assert list(work.iterdir()) == []
