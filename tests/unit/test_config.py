"""
Unit-Tests für die Konfigurationskomponente.

Diese Tests überprüfen die korrekte Funktionsweise der Konfigurationskomponente.
"""

from pathlib import Path

from github_collector import config


def test_config_paths():
    """Testet, ob die Konfigurationspfade korrekt gesetzt sind."""
    # Teste, ob BASE_DIR korrekt gesetzt ist
    expected_base_dir = Path(__file__).resolve().parent.parent.parent
    assert config.BASE_DIR == expected_base_dir

    # Verzeichnisse sollten existieren (können aber per ENV umkonfiguriert sein)
    assert config.DATA_DIR.is_dir()
    assert config.LOG_DIR.is_dir()
    assert config.CACHE_DIR.is_dir()


def test_config_db_settings():
    """Testet, ob die Datenbankeinstellungen korrekt gesetzt sind."""
    # DB_URL sollte eine gültige SQLAlchemy-URL sein
    assert isinstance(config.DB_URL, str)
    assert config.DB_URL
    assert "://" in config.DB_URL


def test_config_log_settings():
    """Testet, ob die Logging-Einstellungen korrekt gesetzt sind."""
    # LOG_CONFIG sollte die zentralen Logging-Einstellungen enthalten
    assert isinstance(config.LOG_CONFIG, dict)
    for key in ["level", "format", "date_format", "files"]:
        assert key in config.LOG_CONFIG
    files = config.LOG_CONFIG["files"]
    # Prüfe, dass alle erwarteten Log-Dateien konfiguriert sind und unterhalb von LOG_DIR liegen
    for name in ["repository", "geocoding", "export", "reset"]:
        assert name in files
        assert str(config.LOG_DIR) in str(files[name])


def test_config_collection_state():
    """Testet, ob die Collection-State-Einstellungen korrekt gesetzt sind."""
    # Teste, ob STATE_CONFIG eine gültige Zustandsdatei konfiguriert
    assert isinstance(config.STATE_CONFIG, dict)
    assert "file" in config.STATE_CONFIG
    state_file = config.STATE_CONFIG["file"]
    assert str(config.DATA_DIR) in str(state_file)


def test_config_performance_tracking():
    """Testet, ob die Performance-Tracking-Einstellungen korrekt gesetzt sind."""
    # Teste, ob PERFORMANCE_CONFIG korrekt aufgebaut ist
    assert isinstance(config.PERFORMANCE_CONFIG, dict)
    assert "enabled" in config.PERFORMANCE_CONFIG
    assert "output_dir" in config.PERFORMANCE_CONFIG
    assert isinstance(config.PERFORMANCE_CONFIG["enabled"], bool)
    output_dir = config.PERFORMANCE_CONFIG["output_dir"]
    assert str(config.LOG_DIR) in str(output_dir)
