"""Unit-Tests für github_collector.utils.secrets.SecretManager.

Diese Tests validieren das Verhalten von:
- get_github_tokens
- validate_github_token (indirekt über get_github_tokens)
- sanitize_db_url

Externe Abhängigkeiten wie os.environ, keyring und getpass werden mit
pytest-monkeypatch gemockt.
"""

from typing import List

import pytest

from github_collector.utils.secrets import SecretManager


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    """Säubert relevante Env-Variablen vor jedem Test."""

    for var in ["GITHUB_API_TOKEN", "GITHUB_API_TOKENS"]:
        monkeypatch.delenv(var, raising=False)


def test_get_github_tokens_from_github_api_tokens(monkeypatch):
    """Tokens werden korrekt aus GITHUB_API_TOKENS (CSV-Liste) geladen."""

    monkeypatch.setenv("GITHUB_API_TOKENS", "ghp_" + "a" * 36 + ", ghp_" + "b" * 36)

    tokens: List[str] = SecretManager.get_github_tokens()

    assert len(tokens) == 2
    assert tokens[0].startswith("ghp_")
    assert tokens[1].startswith("ghp_")


def test_get_github_tokens_from_github_api_token(monkeypatch):
    """Einzelnes Token wird korrekt aus GITHUB_API_TOKEN geladen."""

    token = "ghp_" + "c" * 36
    monkeypatch.setenv("GITHUB_API_TOKEN", token)

    tokens = SecretManager.get_github_tokens()

    assert tokens == [token]


def test_get_github_tokens_from_keyring(monkeypatch):
    """Fallback auf Keyring funktioniert, wenn keine Env-Tokens gesetzt sind."""

    stored_token = "ghp_" + "d" * 36

    class DummyKeyring:  # einfacher Namespace für die Mocks
        @staticmethod
        def get_password(service, username):  # pragma: no cover - Signatur-Doku
            assert service == "github_collector"
            assert username == "api_token"
            return stored_token

        @staticmethod
        def set_password(
            service, username, password
        ):  # sollte in diesem Pfad nicht aufgerufen werden
            pytest.fail("set_password sollte in diesem Test nicht aufgerufen werden")

    monkeypatch.setattr(
        "github_collector.utils.secrets.keyring.get_password", DummyKeyring.get_password
    )
    monkeypatch.setattr(
        "github_collector.utils.secrets.keyring.set_password", DummyKeyring.set_password
    )

    tokens = SecretManager.get_github_tokens()

    assert tokens == [stored_token]


def test_get_github_tokens_from_getpass(monkeypatch):
    """Fallback auf getpass.getpass funktioniert, wenn Env und Keyring leer sind."""

    entered_token = "ghp_" + "e" * 36

    # Env ist über Fixture bereits geleert; Keyring soll hier nichts liefern
    def fake_get_password(service, username):  # pragma: no cover - einfache Kontrollfunktion
        return None

    def fake_set_password(service, username, password):  # pragma: no cover - Best-Effort-Speicher
        # Wir prüfen hier nur, dass der Speicher-Versuch nicht crasht
        assert password == entered_token

    def fake_getpass(prompt=""):
        assert "GitHub API Token" in prompt
        return entered_token

    monkeypatch.setattr("github_collector.utils.secrets.keyring.get_password", fake_get_password)
    monkeypatch.setattr("github_collector.utils.secrets.keyring.set_password", fake_set_password)
    monkeypatch.setattr("github_collector.utils.secrets.getpass.getpass", fake_getpass)

    tokens = SecretManager.get_github_tokens()

    assert tokens == [entered_token]


def test_get_github_tokens_raises_value_error_when_no_token(monkeypatch):
    """Wenn keine Quelle ein gültiges Token liefert, wird ValueError mit klarer Message geworfen."""

    # Env ist leer; Keyring liefert nichts; getpass liefert leeren String
    monkeypatch.setattr(
        "github_collector.utils.secrets.keyring.get_password",
        lambda service, username: None,
    )
    monkeypatch.setattr(
        "github_collector.utils.secrets.getpass.getpass",
        lambda prompt="": "",
    )

    with pytest.raises(ValueError) as excinfo:
        SecretManager.get_github_tokens()

    msg = str(excinfo.value)
    assert "Kein GitHub-Token gefunden" in msg
    assert "GITHUB_API_TOKEN" in msg
    assert "GITHUB_API_TOKENS" in msg


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "mysql+mysqlconnector://user:secret@localhost:3306/github_data",
            "mysql+mysqlconnector://user:****@localhost:3306/github_data",
        ),
        (
            "postgresql://user:secret@db.example.com/mydb",
            "postgresql://user:****@db.example.com/mydb",
        ),
    ],
)
def test_sanitize_db_url_masks_password(url, expected):
    """sanitize_db_url maskiert das Passwort in DB-URLs korrekt."""

    assert SecretManager.sanitize_db_url(url) == expected


def test_sanitize_db_url_without_password_is_unchanged():
    """URLs ohne Passwort werden unverändert zurückgegeben."""

    url = "sqlite:///path/to/database.db"
    assert SecretManager.sanitize_db_url(url) == url

    url2 = "mysql+mysqlconnector://user@localhost:3306/github_data"
    assert SecretManager.sanitize_db_url(url2) == url2
