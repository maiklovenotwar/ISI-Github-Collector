"""Integrationstests für das CLI-Skript collect_repositories.py (Phase 3.3).

Ziel: End-to-End-Flows vom CLI-Einstieg bis zur DB, aber mit gemockter
GitHub-API, damit die Tests schnell und stabil bleiben.

Hinweis: Der eigentliche Persistenz-Beweis (COUNT == 1 etc.) wird in
tests/integration/test_db_persistence.py geführt, da CLI-Argument-Parsing
und globale Singleton-Seiteneffekte den direkten DB-Check erschweren.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.collection.collect_repositories import main


@pytest.mark.integration
@patch(
    "scripts.collection.collect_repositories.SecretManager.get_github_tokens",
    return_value=["dummy_token"],
)
@patch("github_collector.api.github_api.GitHubAPI.search_repositories")
def test_analyze_mode_uses_mocked_search_and_prints_total_count(
    mock_search_repositories,
    mock_get_tokens,
    monkeypatch,
    capsys,
) -> None:
    """--analyze soll die Volumenschätzung nutzen und das Ergebnis ausgeben.

    Die GitHub-API wird über search_repositories gemockt, so dass kein echter
    Netzwerkaufruf erfolgt.
    """

    # Such-API so mocken, dass ein definierter total_count zurückgegeben wird
    mock_search_repositories.return_value = {"total_count": 5000}

    # CLI-Argumente für Analyze-Modus simulieren
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_repositories.py",
            "--analyze",
            "--time-range",
            "month",
            "--db-path",
            "sqlite:///:memory:",
            "--min-stars",
            "100",
        ],
    )

    # main() ausführen
    main()

    # Ausgabe prüfen
    captured = capsys.readouterr()
    out = captured.out
    assert "Gefundene Repositories: 5000" in out
    assert "Empfohlene Perioden" in out


@pytest.mark.integration
@patch(
    "scripts.collection.collect_repositories.SecretManager.get_github_tokens",
    return_value=["dummy_token"],
)
@patch("github_collector.api.github_api.GitHubAPI.monitor_rate_limit", return_value={})
@patch("github_collector.api.github_api.GitHubAPI.get_organization")
@patch("github_collector.api.github_api.GitHubAPI.get_contributor")
@patch("github_collector.api.github_api.GitHubAPI.search_repositories")
def test_non_interactive_mode_runs_with_sqlite_and_mocked_api(
    mock_search_repositories,
    mock_get_contributor,
    mock_get_organization,
    mock_monitor_rate_limit,
    mock_get_tokens,
    monkeypatch,
) -> None:
    """--non-interactive soll mit SQLite-DB und gemockter API durchlaufen.

    Wir mocken die GitHub-API so, dass genau ein Repository zurückgegeben
    wird und Owner-Daten ohne echte Netzwerk-Calls auflösbar sind.
    """

    monkeypatch.setenv("GITHUB_API_TOKENS", "dummy_token")

    # Repository-Such-API: Ein einzelnes, gültiges Repository zurückgeben
    repo = {
        "id": 1,
        "name": "test-repo",
        "full_name": "user/test-repo",
        "owner": {"id": 1, "login": "user", "type": "User"},
        "stargazers_count": 123,
        "created_at": "2023-11-01T12:00:00Z",
        "updated_at": "2023-11-01T12:00:00Z",
    }
    mock_search_repositories.return_value = {"total_count": 1, "items": [repo]}

    # Owner-Auflösung ebenfalls mocken, damit keine echten API-Aufrufe stattfinden
    mock_get_contributor.return_value = {"login": "user", "id": 1}
    mock_get_organization.return_value = None

    # CLI-Argumente für nicht-interaktiven Modus mit In-Memory-SQLite
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_repositories.py",
            "--non-interactive",
            "--time-range",
            "week",
            "--db-path",
            "sqlite:///:memory:",
            "--min-stars",
            "100",
        ],
    )

    # main() sollte ohne Exceptions durchlaufen
    main()


@pytest.mark.integration
@patch(
    "scripts.collection.collect_repositories.SecretManager.get_github_tokens",
    return_value=["dummy_token"],
)
@patch("github_collector.api.github_api.GitHubAPI.monitor_rate_limit", return_value={})
@patch("github_collector.api.github_api.GitHubAPI.get_organization")
@patch("github_collector.api.github_api.GitHubAPI.get_contributor")
@patch("github_collector.api.github_api.GitHubAPI.search_repositories")
def test_non_interactive_mode_with_file_sqlite_runs_without_error(
    mock_search_repositories,
    mock_get_contributor,
    mock_get_organization,
    mock_monitor_rate_limit,
    mock_get_tokens,
    tmp_path,
    monkeypatch,
) -> None:
    """Verifiziere, dass der nicht-interaktive Modus mit Datei-SQLite durchläuft.

    Szenario:
    - Temporäre SQLite-Datei als DB.
    - API liefert genau ein Repository plus einen Owner.
    - Der CLI-Durchlauf soll ohne Exceptions abschließen.

    Hinweis: Der eigentliche Persistenz-Check (COUNT == 1) wird in
    tests/integration/test_db_persistence.py durchgeführt, da dort
    keine CLI-Argument-Parsing-Seiteneffekte stören.
    """

    # Temporäre SQLite-Datei vorbereiten
    db_file: Path = tmp_path / "test_repos.db"
    db_url = f"sqlite:///{db_file}"

    # Repository-Such-API: Ein einzelnes, gültiges Repository zurückgeben
    repo = {
        "id": 42,
        "name": "test-repo",
        "full_name": "test-owner/test-repo",
        "owner": {"id": 1, "login": "test-owner", "type": "User"},
        "stargazers_count": 321,
        "created_at": "2023-10-01T12:00:00Z",
        "updated_at": "2023-10-01T12:00:00Z",
    }
    mock_search_repositories.return_value = {"total_count": 1, "items": [repo]}

    # Owner-Auflösung mocken
    mock_get_contributor.return_value = {"login": "test-owner", "id": 1}
    mock_get_organization.return_value = None

    # CLI-Argumente für nicht-interaktiven Modus mit Datei-SQLite
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_repositories.py",
            "--non-interactive",
            "--time-range",
            "week",
            "--db-path",
            db_url,
            "--min-stars",
            "100",
        ],
    )

    # Sammlung ausführen – Test besteht, wenn keine Exception geworfen wird
    main()

    # Minimale Prüfung: SQLite-Datei wurde erstellt
    assert db_file.exists(), "SQLite-Datei wurde nicht erstellt"
