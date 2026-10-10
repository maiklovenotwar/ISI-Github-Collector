"""Unit-Tests für die Logik des OwnerProcessor (Phase 3.2 - Block 3).

Ziel: Reine Logik-Tests ohne echte API-/DB-Aufrufe.

Fokus:
- Deduplication im Input (process_contributors_batch)
- DB-Filterung (check_contributor_exists verhindert API-Call)
- Flow: API-Daten werden in die DB geschrieben
"""

from __future__ import annotations

from typing import List
from unittest.mock import MagicMock

import pytest

from github_collector.owners.owner_processor import OwnerProcessor


@pytest.fixture
def mock_api() -> MagicMock:
    """Erzeuge einen Mock für die GitHubAPI.

    Wir nutzen hier nur die Methoden, die OwnerProcessor aufruft:
    - get_contributor
    - monitor_rate_limit
    """

    api = MagicMock(name="GitHubAPI")
    api.get_contributor = MagicMock(name="get_contributor")
    # monitor_rate_limit wird in process_contributors_batch vor/nach dem Batch aufgerufen
    api.monitor_rate_limit = MagicMock(name="monitor_rate_limit", return_value={})
    return api


@pytest.fixture
def mock_db() -> MagicMock:
    """Erzeuge einen Mock für die GitHubDatabase.

    OwnerProcessor ruft im Konstruktor _preload_known_owners auf und erwartet:
    - get_all_contributor_logins
    - get_all_organization_logins

    Für die Tests liefern wir leere Listen zurück, damit _known_owners leer bleibt.
    """

    db = MagicMock(name="GitHubDatabase")
    db.get_all_contributor_logins = MagicMock(name="get_all_contributor_logins", return_value=[])
    db.get_all_contributor_ids = MagicMock(name="get_all_contributor_ids", return_value=[])
    db.get_all_organization_logins = MagicMock(name="get_all_organization_logins", return_value=[])
    db.get_all_organization_ids = MagicMock(name="get_all_organization_ids", return_value=[])
    db.check_contributor_exists = MagicMock(name="check_contributor_exists", return_value=False)
    db.insert_contributor = MagicMock(name="insert_contributor")
    return db


@pytest.fixture
def owner_processor(mock_api: MagicMock, mock_db: MagicMock) -> OwnerProcessor:
    """Erzeuge einen OwnerProcessor mit gemockter API und DB.

    PerformanceTracker wird nicht benötigt und bleibt daher None.
    """

    processor = OwnerProcessor(api=mock_api, db=mock_db, performance_tracker=None)
    # Sicherstellen, dass bekannte Owner zu Beginn leer sind
    processor._known_owners = set()
    processor.reset_batch_tracking()
    return processor


class TestOwnerProcessorContributorsLogic:
    """Tests für die Contributor-spezifische Logik des OwnerProcessor."""

    def test_deduplication_in_process_contributors_batch(
        self, owner_processor: OwnerProcessor
    ) -> None:
        """Duplikate im Input sollen nur einmal verarbeitet werden.

        Input: ['user1', 'user1', 'user2']
        Erwartung:
        - process_contributor wird nur für 2 unique Logins aufgerufen.
        - API/DB werden dadurch nur für diese Unique-Logins bemüht.
        """

        # process_contributor separat überwachen, um reine Batch-Logik zu testen
        owner_processor.process_contributor = MagicMock(name="process_contributor")

        logins: List[str] = ["user1", "user1", "user2"]

        owner_processor.process_contributors_batch(logins)

        # Es sollten genau zwei eindeutige Logins verarbeitet werden
        assert owner_processor.process_contributor.call_count == 2
        called_logins = {
            call.args[0] for call in owner_processor.process_contributor.call_args_list
        }
        assert called_logins == {"user1", "user2"}

    def test_db_filtering_avoids_api_call_for_existing_contributor(
        self, owner_processor: OwnerProcessor, mock_db: MagicMock, mock_api: MagicMock
    ) -> None:
        """Contributors, die laut DB existieren, sollen keinen API-Call auslösen.

        Input: ['user_neu', 'user_alt']
        Setup:
        - check_contributor_exists('user_alt') -> True

        Erwartung:
        - get_contributor wird nur für 'user_neu' aufgerufen.
        """

        def check_contributor_exists_side_effect(login: str) -> bool:
            return login == "user_alt"

        mock_db.check_contributor_exists.side_effect = check_contributor_exists_side_effect

        # Wir nutzen hier die reale process_contributor-Implementierung
        logins: List[str] = ["user_neu", "user_alt"]

        owner_processor.process_contributors_batch(logins)

        # get_contributor sollte nur für den neuen Contributor aufgerufen worden sein
        called_logins = [call.args[0] for call in mock_api.get_contributor.call_args_list]
        assert called_logins == ["user_neu"]

    def test_flow_new_contributor_is_inserted_into_db(
        self, owner_processor: OwnerProcessor, mock_db: MagicMock, mock_api: MagicMock
    ) -> None:
        """Neuer Contributor: API-Daten werden geholt und in die DB geschrieben.

        Input: ['user_neu']
        Setup:
        - check_contributor_exists -> False
        - get_contributor('user_neu') -> {'login': 'user_neu', 'id': 123}

        Erwartung:
        - insert_contributor wird mit den gelieferten Daten aufgerufen.
        """

        mock_db.check_contributor_exists.return_value = False
        contributor_data = {"login": "user_neu", "id": 123}
        mock_api.get_contributor.return_value = contributor_data

        owner_processor.process_contributors_batch(["user_neu"])

        # insert_contributor sollte genau einmal mit den von der API gelieferten Daten aufgerufen worden sein
        mock_db.insert_contributor.assert_called_once()
        call_args, call_kwargs = mock_db.insert_contributor.call_args
        # Erster positional arg ist das Contributor-Dict, session ist optional/None
        assert call_args[0] == contributor_data

    def test_case_insensitive_known_owner_avoids_api_call(self, mock_api: MagicMock) -> None:
        """OwnerProcessor soll Logins case-insensitive behandeln.

        Setup:
        - DB meldet einen bekannten Contributor-Login "User".
        - Input im Batch ist "user" (lowercase, wie Pydantic normalisiert).

        Erwartung:
        - process_contributors_batch(["user"]) ruft KEIN get_contributor auf,
          weil der Owner über das vorgeladene _known_owners-Set erkannt wird.
        """

        # Spezieller DB-Mock mit einem bekannten Login in Mixed-Case
        db = MagicMock(name="GitHubDatabase")
        db.get_all_contributor_logins = MagicMock(
            name="get_all_contributor_logins", return_value=["User"]
        )
        db.get_all_contributor_ids = MagicMock(name="get_all_contributor_ids", return_value=[])
        db.get_all_organization_logins = MagicMock(
            name="get_all_organization_logins", return_value=[]
        )
        db.get_all_organization_ids = MagicMock(name="get_all_organization_ids", return_value=[])
        # Fallback-Check sollte hier gar nicht mehr entscheidend sein
        db.check_contributor_exists = MagicMock(name="check_contributor_exists", return_value=False)
        db.insert_contributor = MagicMock(name="insert_contributor")

        processor = OwnerProcessor(api=mock_api, db=db, performance_tracker=None)
        processor.reset_batch_tracking()

        # Mixed-Case im Input (so wie Pydantic nach lower() aussehen würde)
        processor.process_contributors_batch(["user"])

        # Da "User" aus der DB zu "user" normalisiert wurde, darf kein API-Call erfolgen
        mock_api.get_contributor.assert_not_called()
