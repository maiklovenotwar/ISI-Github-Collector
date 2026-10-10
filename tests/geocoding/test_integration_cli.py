from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from github_collector.cli.geocoding_command import update_contributors
from github_collector.database.database import GitHubDatabase
from github_collector.database.models import Contributor


@pytest.fixture()
def db() -> GitHubDatabase:
    """Provide an in-memory GitHubDatabase.

    GitHubDatabase is responsible for creating the schema on
    initialization, so no additional setup is required here.
    """
    db_url = "sqlite:///:memory:"
    return GitHubDatabase(db_url)


@pytest.fixture()
def mock_geocoder() -> MagicMock:
    geocoder = MagicMock()
    geocoder.geocode = MagicMock()
    return geocoder


def _add_contributor(database: GitHubDatabase, login: str, location: str | None) -> int:
    """Helper to insert a single contributor into the test database."""

    def _op(session):
        contributor = Contributor(login=login, location=location, country_code=None)
        session.add(contributor)
        session.commit()
        return contributor.id

    return database._run_in_session(_op)  # type: ignore[attr-defined]


def _get_contributor(database: GitHubDatabase, contributor_id: int) -> Contributor:
    def _op(session):
        return session.get(Contributor, contributor_id)

    return database._run_in_session(_op)  # type: ignore[attr-defined]


def test_update_contributors_integration(db: GitHubDatabase, mock_geocoder: MagicMock) -> None:
    """Integration: update_contributors reads from DB, calls geocoder, writes back."""
    contributor_id = _add_contributor(db, login="alice", location="Berlin")

    mock_geocoder.geocode.return_value = {
        "country_code": "DE",
        "country": "Germany",
        "latitude": 52.52,
        "longitude": 13.405,
    }

    updated_count = update_contributors(db, mock_geocoder, limit=10)

    assert updated_count == 1
    mock_geocoder.geocode.assert_called_once_with("Berlin")

    contributor = _get_contributor(db, contributor_id)
    assert contributor.country_code == "DE"


def test_update_handles_empty_result(db: GitHubDatabase, mock_geocoder: MagicMock) -> None:
    """Integration: non-geographic locations are skipped but marked as processed.

    In der aktuellen CLI-Logik werden bestimmte Standorte (z.B. "Nowhere")
    als nicht-geographisch eingestuft und vor dem Geocoder-Aufruf
    gefiltert. Sie sollen dennoch so aktualisiert werden, dass sie nicht
    erneut von ``get_entities_without_location`` geliefert werden.
    """
    contributor_id = _add_contributor(db, login="bob", location="Nowhere")

    updated_count = update_contributors(db, mock_geocoder, limit=10)

    # Kein erfolgreicher Geocode-Call, daher keine "aktualisierten" Contributors
    assert updated_count == 0
    mock_geocoder.geocode.assert_not_called()

    contributor = _get_contributor(db, contributor_id)
    # CLI markiert Einträge ohne Ergebnis mit leerem Country Code, damit sie
    # nicht erneut von get_entities_without_location geliefert werden.
    assert contributor.country_code == ""
