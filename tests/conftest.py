"""
Pytest-Konfiguration für den GitHub Data Collector.

Diese Datei enthält Fixtures und Konfigurationen für die Tests des GitHub Data Collectors.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Füge das src-Verzeichnis zum Python-Pfad hinzu
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

# Lade .env, damit Konfiguration und Tests die gleichen Umgebungsvariablen sehen
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

# github_collector.config bricht beim Import ab, wenn kein GitHub-Token gesetzt ist.
# Ohne .env (frischer Checkout, CI) genügt ein Platzhalter: die Standard-Tests rufen die
# GitHub-API nicht auf, nur die abgewählten `external`-Tests brauchen ein echtes Token.
if not (os.getenv("GITHUB_API_TOKEN", "").strip() or os.getenv("GITHUB_API_TOKENS", "").strip()):
    os.environ["GITHUB_API_TOKEN"] = "placeholder-token-for-tests"  # pragma: allowlist secret

from github_collector.api.github_api import GitHubAPI  # noqa: E402
from github_collector.database.database import GitHubDatabase, init_db  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_collection_state(tmp_path, monkeypatch):
    """Lenkt die Zustandsdatei der Sammlung für jeden Test in ein temporäres Verzeichnis um.

    Ohne diese Umlenkung verwendet jeder Test, der einen RepositoryCollector ohne eigenen
    Pfad anlegt (z.B. über das CLI-Skript), die echte Datei data/collection_state.json und
    verändert damit den Fortschritt einer tatsächlichen Sammlung.
    """
    from github_collector import config, repository_collector

    redirected = tmp_path / "collection_state.json"
    # Beide Verweise umlenken: Tests, die das config-Modul neu laden, erzeugen ein neues
    # STATE_CONFIG-Dict, während der Collector weiter das beim Import gebundene verwendet.
    monkeypatch.setitem(config.STATE_CONFIG, "file", redirected)
    monkeypatch.setitem(repository_collector.STATE_CONFIG, "file", redirected)


@pytest.fixture
def temp_db_path():
    """Erstellt einen temporären Pfad für eine Testdatenbank."""
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db_path = tmp.name
    return f"sqlite:///{db_path}"


@pytest.fixture
def test_db(temp_db_path):
    """Erstellt eine Testdatenbank und gibt eine Datenbankverbindung zurück."""
    # Initialisiere die Datenbank
    init_db(temp_db_path)

    # Erstelle eine Datenbankverbindung
    db = GitHubDatabase(temp_db_path)

    yield db

    # Bereinige nach dem Test - GitHubDatabase.close() existiert nicht mehr
    # seit der Session-per-Operation-Refaktorisierung (kein `.session`/`.close()`
    # Attribut mehr, siehe database.py). Engine trotzdem explizit disposen,
    # damit die anschließende Datei-Löschung unter Windows nicht an einem
    # offenen SQLite-Handle scheitert.
    db.session_factory.kw["bind"].dispose()

    # Lösche die Datenbankdatei. Best-effort: dieser Pfad wurde vorher nie
    # erreicht (db.close() ist immer schon vorher gecrasht, siehe oben) - erst
    # dadurch sichtbar geworden, dass Windows das SQLite-Handle manchmal noch
    # kurz hält, selbst nach engine.dispose(). Kein Grund, den Test daran
    # scheitern zu lassen - die OS-Temp-Verzeichnis-Bereinigung räumt das
    # sonst ohnehin irgendwann auf.
    db_path = temp_db_path.replace("sqlite:///", "")
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except PermissionError:
            pass


@pytest.fixture
def mock_github_api():
    """Erstellt eine Mock-Version des GitHub API-Clients."""
    # Erstelle einen API-Client mit einem Dummy-Token
    api = GitHubAPI(["dummy_token"])

    # Überschreibe die request-Methode, um keine echten API-Aufrufe zu machen
    original_request = api.request

    def mock_request(method, endpoint, params=None, data=None):
        # Hier könnten je nach Endpoint Mock-Daten zurückgegeben werden
        if endpoint == "/users/octocat":
            return {
                "login": "octocat",
                "id": 1,
                "name": "The Octocat",
                "company": "GitHub",
                "blog": "https://github.blog",
                "location": "San Francisco, CA",
                "email": None,
                "hireable": None,
                "bio": "I'm the GitHub mascot!",
                "twitter_username": None,
                "public_repos": 8,
                "public_gists": 8,
                "followers": 3938,
                "following": 9,
                "created_at": "2011-01-25T18:44:36Z",
                "updated_at": "2023-01-22T12:16:22Z",
            }
        elif endpoint == "/repos/octocat/Hello-World":
            return {
                "id": 1296269,
                "name": "Hello-World",
                "full_name": "octocat/Hello-World",
                "owner": {"login": "octocat", "id": 1, "type": "User"},
                "private": False,
                "description": "My first repository on GitHub!",
                "fork": False,
                "created_at": "2011-01-26T19:01:12Z",
                "updated_at": "2023-01-22T12:16:22Z",
                "pushed_at": "2023-01-22T12:16:22Z",
                "homepage": "https://github.com",
                "size": 108,
                "stargazers_count": 2000,
                "watchers_count": 2000,
                "language": "Python",
                "forks_count": 1000,
                "open_issues_count": 0,
                "default_branch": "main",
            }
        else:
            # Für unbekannte Endpoints leere Antwort zurückgeben
            return {}

    # Ersetze die request-Methode durch die Mock-Version
    api.request = mock_request

    yield api

    # Stelle die ursprüngliche Methode wieder her
    api.request = original_request
