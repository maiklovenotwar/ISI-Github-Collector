"""Tests für die Retry-Integration im GitHub API-Layer.

Dieser Test verifiziert, dass ``GitHubClient.request`` bei
``RequestException``-Fehlern (z.B. ``ConnectionError``) automatisch
mehrere Versuche mit Backoff durchführt und bei einem späteren
Erfolgsergebnis korrekt zurückkehrt.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import Mock

import pytest
from requests import Response
from requests.exceptions import ConnectionError

from github_collector.api.github_api import GitHubClient


def _build_success_response(payload: Dict[str, Any]) -> Response:
    """Hilfsfunktion, um einen erfolgreichen Response zu erzeugen."""
    response = Response()
    response.status_code = 200
    response._content = __import__("json").dumps(payload).encode("utf-8")  # type: ignore[attr-defined]
    response.headers["Content-Type"] = "application/json"
    return response


def test_github_client_request_retries_on_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stellt sicher, dass GitHubClient.request bei ConnectionError erneut versucht wird.

    Szenario:
    - Erster Aufruf von ``session.request`` wirft ``ConnectionError``.
    - Zweiter Aufruf liefert einen gültigen 200er-Response mit JSON-Body.
    Erwartung:
    - ``GitHubClient.request`` gibt die geparste JSON-Antwort zurück.
    - ``session.request`` wurde genau zwei Mal aufgerufen.
    """

    # Instanziiere Client mit Dummy-Token (es werden keine echten Requests gesendet)
    client = GitHubClient(token="dummy-token")

    # Erwartete Nutzdaten
    expected_payload: Dict[str, Any] = {"ok": True, "value": 42}

    # Baue einen erfolgreichen Response für den zweiten Aufruf
    success_response = _build_success_response(expected_payload)

    # Mocke die session.request-Methode: zuerst ConnectionError, dann Erfolg
    request_mock: Mock[[Any], Any] = Mock()
    request_mock.side_effect = [ConnectionError("simulierter Verbindungsfehler"), success_response]

    monkeypatch.setattr(client.session, "request", request_mock)

    # Führe die Anfrage aus
    result = client.request(
        method="GET",
        endpoint="/test/endpoint",
        params={"q": "test"},
        data=None,
        use_cache=False,
    )

    # Ergebnis sollte dem erwarteten Payload entsprechen
    assert result == expected_payload

    # Die interne requests-Methode sollte genau zwei Mal aufgerufen worden sein
    assert request_mock.call_count == 2
