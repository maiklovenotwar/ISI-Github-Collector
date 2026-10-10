import logging
import os
import sys
from datetime import datetime, timedelta

import pytest

from github_collector.api.github_api import GitHubAPI  # type: ignore
from github_collector.database.database import GitHubDatabase  # type: ignore
from github_collector.repository_collector import RepositoryCollector  # type: ignore

# Stelle sicher, dass das Projektverzeichnis im Pfad ist, falls das Skript direkt aufgerufen wird
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

logger = logging.getLogger(__name__)


@pytest.mark.smoke
@pytest.mark.external
def test_repository_collector_smoke():
    """Smoke-Test für den RepositoryCollector.

    Führt einen stark eingeschränkten Sammellauf aus, um das Zusammenspiel von
    Konfiguration, GitHubAPI, Datenbank und RepositoryCollector zu prüfen.

    Voraussetzungen:
    - Ein gültiges GITHUB_API_TOKEN ist in den Umgebungsvariablen gesetzt
      (wird über tests/conftest.py aus .env geladen).
    """
    token = os.getenv("GITHUB_API_TOKEN")
    if not token:
        pytest.skip("GITHUB_API_TOKEN nicht gesetzt – Smoke-Test wird übersprungen.")

    # Sehr kleiner, aktueller Zeitraum, um den Lauf kurz zu halten
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=1)

    # Datenbank initialisieren – benutzt die Konfiguration aus config.py
    db = GitHubDatabase()

    # GitHub API und RepositoryCollector initialisieren
    api = GitHubAPI(tokens=[token])
    collector = RepositoryCollector(github_client=api, db=db, enable_performance_tracking=False)

    # Maximal ein Repository sammeln, um API-Limits zu schonen
    max_repos_to_collect = 1

    try:
        collected = collector.collect_repositories(
            start_date=start_date,
            end_date=end_date,
            min_stars=0,
            max_repos=max_repos_to_collect,
            resume=False,
        )
    except Exception as exc:  # pragma: no cover - reine Smoke-/Diagnoselogik
        logger.error("RepositoryCollector Smoke-Test fehlgeschlagen: %s", exc)
        pytest.fail(f"RepositoryCollector Smoke-Test fehlgeschlagen: {exc}")

    # Der Aufruf soll ohne Ausnahme durchlaufen; die genaue Anzahl ist zweitrangig,
    # aber wir loggen sie zur Kontrolle.
    logger.info(
        "RepositoryCollector Smoke-Test erfolgreich – gesammelte Repositories: %s", collected
    )
    assert collected >= 0
