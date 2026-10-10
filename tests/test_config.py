"""
Unit-Tests für die zentrale Konfiguration (github_collector.config).

Diese Tests überprüfen insbesondere die validate_config()-Funktion und stellen
sicher, dass ungültige Konfigurationen frühzeitig mit klaren Exceptions
abgefangen werden.
"""

import importlib
from typing import Dict

import pytest


def _load_config_with_env(monkeypatch: pytest.MonkeyPatch, env: Dict[str, str]):
    """
    Hilfsfunktion: Setzt angegebene Umgebungsvariablen und lädt
    github_collector.config neu, sodass validate_config() beim Import greift.
    """
    # Nur die übergebenen Keys setzen/überschreiben – bestehende Variablen
    # bleiben erhalten, um Seiteneffekte zu minimieren.
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    import github_collector.config as cfg_module  # type: ignore

    cfg_module = importlib.reload(cfg_module)
    return cfg_module


def test_validate_config_requires_token(monkeypatch: pytest.MonkeyPatch):
    """
    validate_config() muss fehlschlagen, wenn weder GITHUB_API_TOKEN noch
    GITHUB_API_TOKENS gesetzt sind.
    """
    # Sicherstellen, dass beide Token-Quellen leer sind
    env = {
        "GITHUB_API_TOKEN": "",
        "GITHUB_API_TOKENS": "",
    }

    with pytest.raises(ValueError) as excinfo:
        _load_config_with_env(monkeypatch, env)

    assert "Mindestens ein GitHub API Token" in str(excinfo.value)


def test_validate_config_rejects_page_size_above_limit(monkeypatch: pytest.MonkeyPatch):
    """
    validate_config() muss fehlschlagen, wenn GITHUB_PAGE_SIZE > 100 ist.
    """
    env = {
        "GITHUB_API_TOKEN": "dummy-token",
        "GITHUB_API_TOKENS": "",
        "GITHUB_PAGE_SIZE": "101",
    }

    with pytest.raises(ValueError) as excinfo:
        _load_config_with_env(monkeypatch, env)

    assert "GITHUB_PAGE_SIZE darf maximal 100 sein" in str(excinfo.value)


def test_validate_config_rejects_timeout_below_one(monkeypatch: pytest.MonkeyPatch):
    """
    validate_config() muss fehlschlagen, wenn GITHUB_API_TIMEOUT < 1 ist.
    """
    env = {
        "GITHUB_API_TOKEN": "dummy-token",
        "GITHUB_API_TOKENS": "",
        "GITHUB_API_TIMEOUT": "0",
    }

    with pytest.raises(ValueError) as excinfo:
        _load_config_with_env(monkeypatch, env)

    assert "GITHUB_API_TIMEOUT muss mindestens 1 Sekunde sein" in str(excinfo.value)


def test_validate_config_rejects_negative_retries(monkeypatch: pytest.MonkeyPatch):
    """
    validate_config() muss fehlschlagen, wenn GITHUB_API_RETRIES negativ ist.
    """
    env = {
        "GITHUB_API_TOKEN": "dummy-token",
        "GITHUB_API_TOKENS": "",
        "GITHUB_API_RETRIES": "-1",
    }

    with pytest.raises(ValueError) as excinfo:
        _load_config_with_env(monkeypatch, env)

    assert "GITHUB_API_RETRIES darf nicht negativ sein" in str(excinfo.value)


def test_validate_config_raises_permission_error(monkeypatch: pytest.MonkeyPatch):
    """
    validate_config() muss einen PermissionError werfen, wenn auf eines der
    konfigurierten Verzeichnisse keine Schreibrechte bestehen.
    """
    import github_collector.config as cfg_module  # type: ignore

    # os.access in dem bereits importierten Modul mocken
    def fake_access(path, mode):
        # Simuliere: niemals Schreibrechte
        return False

    monkeypatch.setattr("github_collector.config.os.access", fake_access)

    # Reload triggert validate_config() erneut
    with pytest.raises(PermissionError):
        importlib.reload(cfg_module)
