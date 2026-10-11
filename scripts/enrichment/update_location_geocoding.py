#!/usr/bin/env python3
"""
Skript zur Aktualisierung der Geocoding-Informationen für Contributor- und Organisationsstandorte.

Dieses Skript dient als Einstiegspunkt für die Geocoding-Anreicherung des GitHub Data Collectors.
Es ruft die CLI-Implementierung auf, verarbeitet Kommandozeilenargumente und schreibt die Ergebnisse
sowie alle Logs in die Datenbank und ins Logfile `logs/geocoding/geocoding.log`.

Besonderheiten:
- Nutzt einen lokalen Cache zur Reduktion von API-Aufrufen (geocoding_cache.json)
- Unterstützt gezielte Geocodierung für Contributors und/oder Organisationen
- Unterstützt Limitierung, erneute Geocodierung und Cache-Reset
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from github_collector import config
from github_collector.cli.geocoding_command import main
from github_collector.utils.logging_config import setup_logging

logger = setup_logging(log_file=config.LOG_CONFIG["files"]["geocoding"])


if __name__ == "__main__":
    sys.exit(main())
