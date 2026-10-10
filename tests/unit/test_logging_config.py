"""
Unit-Tests für die Logging-Konfiguration.

Diese Tests überprüfen die korrekte Funktionsweise der Logging-Konfiguration.
"""

import logging

from github_collector import config
from github_collector.utils.logging_config import (
    get_export_logger,
    get_geocoding_logger,
    get_repository_logger,
    setup_logging,
)


def test_setup_logging_default():
    """Testet die setup_logging-Funktion mit Standardparametern."""
    # Erstelle einen Logger mit Standardparametern
    logger = setup_logging()

    # Teste, ob der Logger korrekt erstellt wurde
    assert isinstance(logger, logging.Logger)
    # Logger-Level sollte aus LOG_CONFIG abgeleitet sein
    configured_level = getattr(logging, config.LOG_CONFIG["level"].upper(), logging.INFO)
    assert logger.level == configured_level

    # Teste, ob der Root-Logger mindestens einen Handler hat
    root_logger = logging.getLogger()
    assert len(root_logger.handlers) >= 1
    assert isinstance(root_logger.handlers[0], logging.Handler)


def test_setup_logging_with_file():
    """Testet die setup_logging-Funktion mit einer Log-Datei."""
    # Erstelle einen temporären Pfad für die Log-Datei
    log_file = config.LOG_DIR / "test_logging.log"

    # Erstelle einen Logger mit Log-Datei
    logger = setup_logging(log_file=log_file)

    # Teste, ob der Logger korrekt erstellt wurde
    assert isinstance(logger, logging.Logger)

    # Teste, ob der Root-Logger mindestens zwei Handler hat (Konsole und Datei)
    root_logger = logging.getLogger()
    assert len(root_logger.handlers) >= 2

    # Teste, ob mindestens ein FileHandler dabei ist
    file_handlers = [h for h in root_logger.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) >= 1

    # Bereinige nach dem Test
    try:
        if log_file.exists():
            log_file.unlink()
    except Exception:
        pass


def test_get_repository_logger():
    """Testet die get_repository_logger-Funktion."""
    # Erstelle einen Repository-Logger
    logger = get_repository_logger()

    # Teste, ob der Logger korrekt erstellt wurde
    assert isinstance(logger, logging.Logger)

    # Teste, ob der Root-Logger mindestens zwei Handler hat (Konsole und Datei)
    root_logger = logging.getLogger()
    assert len(root_logger.handlers) >= 2

    # Teste, ob mindestens ein FileHandler dabei ist
    file_handlers = [h for h in root_logger.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) >= 1

    # Teste, ob der FileHandler auf die richtige Datei zeigt
    repo_log = config.LOG_CONFIG["files"]["repository"]
    for handler in file_handlers:
        assert str(repo_log) in handler.baseFilename


def test_get_geocoding_logger():
    """Testet die get_geocoding_logger-Funktion."""
    # Erstelle einen Geocoding-Logger
    logger = get_geocoding_logger()

    # Teste, ob der Logger korrekt erstellt wurde
    assert isinstance(logger, logging.Logger)

    # Teste, ob der Root-Logger mindestens zwei Handler hat (Konsole und Datei)
    root_logger = logging.getLogger()
    assert len(root_logger.handlers) >= 2

    # Teste, ob mindestens ein FileHandler dabei ist
    file_handlers = [h for h in root_logger.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) >= 1

    # Teste, ob der FileHandler auf die richtige Datei zeigt
    geocoding_log = config.LOG_CONFIG["files"]["geocoding"]
    for handler in file_handlers:
        assert str(geocoding_log) in handler.baseFilename


def test_get_export_logger():
    """Testet die get_export_logger-Funktion."""
    # Erstelle einen Export-Logger
    logger = get_export_logger()

    # Teste, ob der Logger korrekt erstellt wurde
    assert isinstance(logger, logging.Logger)

    # Teste, ob der Root-Logger mindestens zwei Handler hat (Konsole und Datei)
    root_logger = logging.getLogger()
    assert len(root_logger.handlers) >= 2

    # Teste, ob mindestens ein FileHandler dabei ist
    file_handlers = [h for h in root_logger.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) >= 1

    # Teste, ob der FileHandler auf die richtige Datei zeigt
    export_log = config.LOG_CONFIG["files"]["export"]
    for handler in file_handlers:
        assert str(export_log) in handler.baseFilename
