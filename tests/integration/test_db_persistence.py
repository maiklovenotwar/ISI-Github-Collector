"""Component-Integration-Test für die Datenpersistenz (Phase 3.3).

Testet das Zusammenspiel von RepositoryCollector und GitHubDatabase direkt,
ohne CLI-Wrapper. Dies ist der stabile Beweis, dass unsere Pipeline Daten
korrekt persistiert:

    Mock-API → RepositoryCollector → Pydantic → OwnerProcessor → DB-Upsert

Hinweis: Da _process_repositories_batch intern session_scope() ohne Argument
aufruft (was die globale SessionLocal verwendet), müssen wir im Test die
globale SessionLocal auf die Test-DB patchen.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import github_collector.database.database as db_module
from github_collector.database.database import GitHubDatabase, _create_engine
from github_collector.database.models import Contributor, Repository
from github_collector.repository_collector import RepositoryCollector


@pytest.mark.integration
def test_repository_collector_persists_to_sqlite(tmp_path: Path) -> None:
    """Verifiziere, dass RepositoryCollector Daten korrekt in SQLite persistiert.

    Szenario:
    - Temporäre SQLite-Datei als DB.
    - Mock-API liefert Rate-Limit-Info und Contributor-Metadaten.
    - Ein valides Repository mit Owner wird über _process_repositories_batch verarbeitet.
    - Danach: Genau 1 Repository und mindestens 1 Contributor in der DB.
    """

    # === SETUP ===

    # Temporäre SQLite-Datei vorbereiten
    db_file: Path = tmp_path / "real_test.db"
    db_url = f"sqlite:///{db_file}"

    # Globale SessionLocal auf die Test-DB patchen, da _process_repositories_batch
    # intern session_scope() ohne Argument aufruft (nutzt globale SessionLocal)
    test_engine = _create_engine(db_url)
    db_module._engine = test_engine
    db_module.SessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)

    # GitHubDatabase instanziieren (erzeugt Tabellen)
    db = GitHubDatabase(db_url)

    # Mock-API erstellen
    mock_api = MagicMock()

    # Rate-Limit-Monitoring mocken (wird in _process_repositories_batch aufgerufen)
    mock_api.monitor_rate_limit.return_value = {"has_critical": False}

    # Contributor-Metadaten-Abruf mocken (wird in _collect_contributors_metadata_batch aufgerufen)
    # Das get_contributor gibt erweiterte User-Infos zurück
    mock_api.get_contributor.return_value = {
        "id": 1001,
        "login": "test-owner",
        "name": "Test Owner",
        "type": "User",
        "email": "test@example.com",
        "location": "Berlin, Germany",
        "company": "Test Company",
        "bio": "A test user",
        "public_repos": 42,
        "followers": 100,
        "following": 50,
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2023-01-01T00:00:00Z",
    }

    # Organisation-Abruf mocken (falls Owner-Type Organization wäre)
    mock_api.get_organization.return_value = None

    # RepositoryCollector mit Mock-API und echter DB instanziieren
    collector = RepositoryCollector(github_client=mock_api, db=db)

    # === EXECUTION ===

    # Valides Repository-Dictionary (wie von GitHub API zurückgegeben)
    mock_repo_data = {
        "id": 12345,
        "name": "awesome-project",
        "full_name": "test-owner/awesome-project",
        "owner": {
            "id": 1001,
            "login": "test-owner",
            "type": "User",
        },
        "description": "An awesome test project",
        "language": "Python",
        "stargazers_count": 500,
        "watchers_count": 500,
        "forks_count": 50,
        "open_issues_count": 10,
        "private": False,
        "fork": False,
        "created_at": "2022-01-15T10:00:00Z",
        "updated_at": "2023-06-01T12:00:00Z",
        "pushed_at": "2023-06-01T11:00:00Z",
    }

    # Verarbeite das Repository über den Batch-Prozessor
    # Dies durchläuft: Pydantic-Validierung → Owner-Extraktion → Bulk-Upsert
    collector._process_repositories_batch([mock_repo_data])

    # === VERIFICATION ===

    # Neue SQLAlchemy-Engine/Session auf dieselbe Datei erstellen
    # (unabhängig von der GitHubDatabase-Instanz)
    assert db_file.exists(), "SQLite-Datei wurde nicht erstellt"

    engine = create_engine(db_url)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        # Repositories: genau ein Eintrag
        repo_count = session.query(Repository).count()
        assert repo_count == 1, f"Erwartet 1 Repository, gefunden: {repo_count}"

        # Prüfe full_name
        repo = session.query(Repository).first()
        assert repo is not None
        assert repo.full_name == "test-owner/awesome-project"
        assert repo.stargazers_count == 500
        assert repo.language == "Python"

        # Contributors: mindestens ein Eintrag (der Owner)
        contrib_count = session.query(Contributor).count()
        assert contrib_count >= 1, f"Erwartet mindestens 1 Contributor, gefunden: {contrib_count}"

        # Prüfe, dass der Owner als Contributor existiert
        owner = session.query(Contributor).filter_by(login="test-owner").first()
        assert owner is not None, "Owner 'test-owner' nicht in contributors gefunden"
        assert owner.id == 1001


@pytest.mark.integration
def test_multiple_repositories_persist_correctly(tmp_path: Path) -> None:
    """Verifiziere, dass mehrere Repositories korrekt persistiert werden.

    Testet auch Deduplizierung: Derselbe Owner für mehrere Repos sollte
    nur einmal in contributors landen.
    """

    # === SETUP ===
    db_file: Path = tmp_path / "multi_test.db"
    db_url = f"sqlite:///{db_file}"

    # Globale SessionLocal auf die Test-DB patchen
    test_engine = _create_engine(db_url)
    db_module._engine = test_engine
    db_module.SessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)

    db = GitHubDatabase(db_url)

    mock_api = MagicMock()
    mock_api.monitor_rate_limit.return_value = {"has_critical": False}
    mock_api.get_contributor.return_value = {
        "id": 2001,
        "login": "shared-owner",
        "type": "User",
    }
    mock_api.get_organization.return_value = None

    collector = RepositoryCollector(github_client=mock_api, db=db)

    # === EXECUTION ===

    # Zwei Repositories vom selben Owner
    repos = [
        {
            "id": 100,
            "name": "repo-one",
            "full_name": "shared-owner/repo-one",
            "owner": {"id": 2001, "login": "shared-owner", "type": "User"},
            "stargazers_count": 100,
            "created_at": "2022-01-01T00:00:00Z",
            "updated_at": "2023-01-01T00:00:00Z",
        },
        {
            "id": 200,
            "name": "repo-two",
            "full_name": "shared-owner/repo-two",
            "owner": {"id": 2001, "login": "shared-owner", "type": "User"},
            "stargazers_count": 200,
            "created_at": "2022-02-01T00:00:00Z",
            "updated_at": "2023-02-01T00:00:00Z",
        },
    ]

    collector._process_repositories_batch(repos)

    # === VERIFICATION ===
    engine = create_engine(db_url)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        # Zwei Repositories
        repo_count = session.query(Repository).count()
        assert repo_count == 2, f"Erwartet 2 Repositories, gefunden: {repo_count}"

        # Aber nur ein Contributor (Deduplizierung)
        contrib_count = session.query(Contributor).count()
        assert (
            contrib_count == 1
        ), f"Erwartet 1 Contributor (dedupliziert), gefunden: {contrib_count}"

        # Beide Repos haben denselben Owner
        repos_in_db = session.query(Repository).all()
        owner_ids = {r.owner_id for r in repos_in_db}
        assert len(owner_ids) == 1, "Beide Repos sollten denselben owner_id haben"
