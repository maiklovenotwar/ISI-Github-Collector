"""Konfiguration des Loggings für den GitHub Data Collector.

Dieses Modul bietet eine einheitliche Konfiguration für das Logging in allen Teilen
des GitHub Data Collectors. Es ermöglicht eine konsistente Formatierung und Handhabung
von Log-Nachrichten über das gesamte Projekt hinweg.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from github_collector import config


def setup_logging(
    log_file: Optional[str] = None, level: Optional[int] = None, logger_name: Optional[str] = None
) -> logging.Logger:
    """Richtet das zentrale Logging ein und gibt einen Logger zurück.

    Die Handler (Konsole + rotierende Log-Datei) werden auf dem Root-Logger
    konfiguriert. Dadurch schreiben alle Modul-Logger, die über
    ``logging.getLogger(__name__)`` erstellt werden, automatisch in die
    konfigurierten Handler.

    Args:
        log_file: Pfad zur Log-Datei (optional).
        level: Logging-Level (optional).
        logger_name: Name des Loggers (optional). Wenn nicht angegeben,
            wird der Name des aufrufenden Moduls verwendet.

    Returns:
        Logger-Instanz, die für das Logging verwendet werden kann.
    """
    # Verwende Konfigurationsparameter, wenn nicht explizit angegeben
    if level is None:
        level_str = config.LOG_CONFIG["level"].upper()
        level = getattr(logging, level_str, logging.INFO)

    # Bestimme den Logger-Namen (nur für die Rückgabe relevant)
    if logger_name is None:
        import inspect

        caller_frame = inspect.stack()[1]
        module = inspect.getmodule(caller_frame[0])
        logger_name = module.__name__ if module else "__main__"

    # Konfiguriere den Root-Logger, damit alle Modul-Logger darüber loggen
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Entferne bestehende Handler am Root-Logger, um Duplikate zu vermeiden
    if root_logger.handlers:
        root_logger.handlers.clear()

    # Erstelle Formatter für konsistentes Log-Format
    formatter = logging.Formatter(
        fmt=config.LOG_CONFIG["format"],
        datefmt=config.LOG_CONFIG["date_format"],
    )

    # Erstelle und konfiguriere Konsolen-Handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Erstelle und konfiguriere Datei-Handler, falls log_file angegeben
    if log_file:
        log_file_path = Path(log_file) if isinstance(log_file, str) else log_file

        # Stelle sicher, dass das Verzeichnis existiert
        log_dir = log_file_path.parent
        log_dir.mkdir(parents=True, exist_ok=True)

        # Rotierende Log-Datei basierend auf Konfiguration
        max_bytes = config.LOG_CONFIG.get("max_size", 10 * 1024 * 1024)
        backup_count = config.LOG_CONFIG.get("backup_count", 5)

        file_handler = RotatingFileHandler(
            log_file_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Gib einen benannten Logger zurück, der über den Root-Logger propagiert
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    return logger


def get_repository_logger() -> logging.Logger:
    """Erstellt einen Logger für die Repository-Sammlung."""
    return setup_logging(log_file=config.LOG_CONFIG["files"]["repository"])


def get_geocoding_logger() -> logging.Logger:
    """Erstellt einen Logger für die Geocoding-Funktionalität."""
    return setup_logging(log_file=config.LOG_CONFIG["files"]["geocoding"])


def get_export_logger() -> logging.Logger:
    """Erstellt einen Logger für die Export-Funktionalität."""
    return setup_logging(log_file=config.LOG_CONFIG["files"]["export"])
