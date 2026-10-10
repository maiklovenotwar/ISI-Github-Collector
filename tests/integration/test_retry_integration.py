"""Tests für die Retry-Integration im Datenbank-Layer.

Dieser Test verifiziert, dass ``GitHubDatabase._run_in_session`` bei
``OperationalError``-Exceptions automatisch erneut versucht wird und
schlussendlich das erfolgreiche Ergebnis zurückliefert.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

from sqlalchemy.exc import OperationalError

from github_collector.database.database import GitHubDatabase


class DummyOperationalError(OperationalError):
    """Hilfsklasse, um einen OperationalError mit minimalen Dummy-Werten zu erzeugen."""

    def __init__(self, message: str = "simulierter OperationalError") -> None:
        # OperationalError erfordert (statement, params, orig)
        super().__init__(message, None, None)


def test_run_in_session_retries_on_operational_error() -> None:
    """Stellt sicher, dass _run_in_session bei OperationalError erneut versucht wird.

    Der Test simuliert eine Operation, die beim ersten Aufruf einen
    OperationalError wirft und beim zweiten Aufruf erfolgreich ist.
    Erwartet wird, dass _run_in_session das erfolgreiche Ergebnis
    zurückliefert und die Operation genau zwei Mal aufgerufen wurde.
    """

    # Nutze eine In-Memory-SQLite-Datenbank
    db = GitHubDatabase(db_path="sqlite:///:memory:")

    # Ergebnis, das die Operation bei Erfolg zurückgeben soll
    expected_result = {"status": "ok"}

    # Erzeuge einen Mock für die Operation: erst OperationalError, dann Erfolg
    operation_mock: Mock[[Any], Any] = Mock()
    operation_mock.side_effect = [DummyOperationalError(), expected_result]

    # Führe die Operation über _run_in_session aus
    result = db._run_in_session(operation_mock)

    # Erwartung: Ergebnis ist der erfolgreiche Rückgabewert
    assert result == expected_result

    # Erwartung: Die Operation wurde genau zwei Mal aufgerufen
    assert operation_mock.call_count == 2
