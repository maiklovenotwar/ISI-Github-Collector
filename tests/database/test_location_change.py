"""Updating an owner with a new location resets its country for the geocoding (K3).

When an organization or account is collected again, `insert_organization`/`insert_contributor`
overwrite the stored location. A country determined for the old location no longer fits.
"""

import pytest

from github_collector.database.database import GitHubDatabase, session_scope
from github_collector.database.models import Contributor, Organization


@pytest.fixture
def db(tmp_path):
    database = GitHubDatabase(f"sqlite:///{tmp_path / 'location.db'}")
    with session_scope(database.session_factory) as session:
        session.add(
            Organization(id=1, login="org", location="Berlin", country_code="DE", region="Berlin")
        )
        session.add(
            Contributor(
                id=2, login="user", type="User", location="Berlin", country_code="DE", region="BE"
            )
        )
    return database


def _get(db, model, row_id):
    with session_scope(db.session_factory) as session:
        row = session.get(model, row_id)
        session.expunge(row)
        return row


@pytest.mark.parametrize(
    "model, insert, row_id, login",
    [
        (Organization, "insert_organization", 1, "org"),
        (Contributor, "insert_contributor", 2, "user"),
    ],
)
def test_update_with_new_location_resets_country(db, model, insert, row_id, login):
    getattr(db, insert)({"id": row_id, "login": login, "location": "Paris"})
    row = _get(db, model, row_id)
    assert row.location == "Paris"
    assert row.country_code is None
    assert row.region is None


@pytest.mark.parametrize(
    "model, insert, row_id, login",
    [
        (Organization, "insert_organization", 1, "org"),
        (Contributor, "insert_contributor", 2, "user"),
    ],
)
def test_update_with_same_location_keeps_country(db, model, insert, row_id, login):
    getattr(db, insert)({"id": row_id, "login": login, "location": " berlin "})
    assert _get(db, model, row_id).country_code == "DE"


def test_update_without_location_keeps_country(db):
    db.insert_organization({"id": 1, "login": "org", "name": "Org"})
    assert _get(db, Organization, 1).country_code == "DE"


def test_geocoding_update_with_country_keeps_it(db):
    org = _get(db, Organization, 1)
    db.update_organization(org, {"location": "Paris", "country_code": "FR", "region": "IDF"})
    row = _get(db, Organization, 1)
    assert (row.country_code, row.region) == ("FR", "IDF")
