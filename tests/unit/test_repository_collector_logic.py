"""Unit-Tests für die Logik des RepositoryCollector (Phase 3.2).

Ziel: Reine Logik-Tests ohne echte API-/DB-Aufrufe.
"""

from datetime import datetime, timedelta
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from github_collector.repository_collector import RepositoryCollector


@pytest.fixture
def mock_api() -> MagicMock:
    """Erzeuge einen Mock für GitHubAPI.

    Wir nutzen nur die Methoden, die in den Tests indirekt aufgerufen werden,
    daher reicht ein MagicMock ohne konkrete Implementierung.
    """

    api = MagicMock(name="GitHubAPI")
    return api


@pytest.fixture
def mock_db() -> MagicMock:
    """Erzeuge einen Mock für GitHubDatabase."""

    return MagicMock(name="GitHubDatabase")


@pytest.fixture
def collector(mock_api: MagicMock, mock_db: MagicMock) -> RepositoryCollector:
    """Erzeuge einen RepositoryCollector mit gemockten Abhängigkeiten.

    Wichtig: Wir wollen keine echten IO-Operationen (State-Datei, DB, API),
    daher mocken wir zusätzlich den CollectionState-Pfad indirekt über einen
    Fake-State in der Instanz.
    """

    c = RepositoryCollector(github_client=mock_api, db=mock_db, state_file="/dev/null")

    # Verhindere echte State-Datei-Operationen
    c.state.state = {
        "time_periods": [],
        "current_period_index": 0,
        "current_period_page": 1,
        "repositories_collected": 0,
    }
    c.state.save = MagicMock(name="save")
    c.state.update = MagicMock(name="update")

    return c


class TestPeriodSplittingLogic:
    """Tests für _split_and_reschedule_period (die Aufteilung als Ganzes prüft
    test_repository_collector_splitting.py)."""

    def test_split_and_reschedule_period_replaces_current_period_with_subperiods(
        self, collector: RepositoryCollector
    ):
        """_split_and_reschedule_period soll die aktuelle Periode korrekt ersetzen.

        Wir testen nur die Manipulation des States und nicht die konkrete
        Berechnung der Sub-Perioden (diese ist bereits in _adjust_period_size
        gekapselt und separat getestet).
        """

        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 11)

        # Ausgangs-State: eine große Periode + eine nachfolgende
        original_periods: list[Dict[str, Any]] = [
            {"start": start.isoformat(), "end": end.isoformat()},
            {
                "start": datetime(2024, 1, 11).isoformat(),
                "end": datetime(2024, 1, 20).isoformat(),
            },
        ]
        collector.state.state["time_periods"] = list(original_periods)
        collector.state.state["current_period_index"] = 0

        # Mock _adjust_period_size, um deterministische Sub-Perioden zu liefern
        sub_start_1 = start
        sub_end_1 = start + timedelta(days=5)
        sub_start_2 = sub_end_1
        sub_end_2 = end

        collector._adjust_period_size = MagicMock(
            return_value=[(sub_start_1, sub_end_1), (sub_start_2, sub_end_2)]
        )

        # Führe Split aus
        result = collector._split_and_reschedule_period(start, end, total_count=1500)

        # Rückgabewert: 0 signalisiert "Periode wurde gesplittet"
        assert result == 0

        # State sollte aktualisiert worden sein
        updated_periods = collector.state.state["time_periods"]
        assert len(updated_periods) == 3

        # Die erste ursprüngliche Periode wurde durch zwei Sub-Perioden ersetzt
        assert updated_periods[0]["start"] == sub_start_1.isoformat()
        assert updated_periods[0]["end"] == sub_end_1.isoformat()
        assert updated_periods[1]["start"] == sub_start_2.isoformat()
        assert updated_periods[1]["end"] == sub_end_2.isoformat()

        # Die nachfolgende Periode blieb erhalten
        assert updated_periods[2] == original_periods[1]

        # current_period_index und current_period_page wurden zurückgesetzt/erhalten
        assert collector.state.state["current_period_index"] == 0
        assert collector.state.state["current_period_page"] == 1


class TestMaxReposAbortLogic:
    """Tests für Abbruchlogik bei max_repos in Kombination mit Star-Range-Sammlung."""

    def test_paginate_and_collect_stops_when_max_repos_reached(
        self, collector: RepositoryCollector
    ):
        """Collector soll stoppen, wenn max_repos erreicht ist.

        Wir simulieren _collect_repositories_in_period_by_star_range indirekt über
        collect_repositories_by_star_range und überwachen, dass keine weiteren
        Perioden verarbeitet werden, sobald das Limit überschritten ist.
        """

        # Setup: Zwei Perioden im State
        period1_start = datetime(2024, 1, 1)
        period1_end = datetime(2024, 1, 5)
        period2_start = datetime(2024, 1, 5)
        period2_end = datetime(2024, 1, 10)

        collector.state.state["time_periods"] = [
            {"start": period1_start.isoformat(), "end": period1_end.isoformat()},
            {"start": period2_start.isoformat(), "end": period2_end.isoformat()},
        ]
        collector.state.state["current_period_index"] = 0
        collector.state.state["repositories_collected"] = 0

        # _initialize_or_resume_star_range_collection soll den State nicht verändern
        collector._initialize_or_resume_star_range_collection = MagicMock()

        # Simuliere, dass in der ersten Periode 50 Repos gesammelt werden
        # und in der zweiten Periode weitere 50 möglich wären.
        def collect_period_side_effect(start_date, end_date, min_stars, max_stars, max_repos):
            if start_date == period1_start:
                return 50
            # Sollte bei korrektem Verhalten gar nicht mehr aufgerufen werden,
            # weil max_repos bereits erreicht ist.
            return 50

        collector._collect_repositories_in_period_by_star_range = MagicMock(
            side_effect=collect_period_side_effect
        )

        # Führe Sammlung mit max_repos=50 aus
        total_collected = collector.collect_repositories_by_star_range(
            start_date=period1_start,
            end_date=period2_end,
            min_stars=0,
            max_stars=100,
            max_repos=50,
            resume=False,
        )

        # Es sollten nur 50 Repos gemeldet werden
        assert total_collected == 50

        # Die period-spezifische Collect-Methode sollte nur einmal (für Periode 1) aufgerufen werden
        calls = collector._collect_repositories_in_period_by_star_range.call_args_list
        assert len(calls) == 1
        call_kwargs = calls[0].kwargs
        assert call_kwargs["start_date"] == period1_start
        assert call_kwargs["end_date"] == period1_end
        assert call_kwargs["max_repos"] == 50
