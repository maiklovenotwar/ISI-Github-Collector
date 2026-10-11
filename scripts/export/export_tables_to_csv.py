#!/usr/bin/env python3
"""Skript zum Exportieren von Datenbanktabellen in CSV-Dateien.

Dünner Wrapper für die CLI-Funktionalität des GitHub Data Collectors.
Es ruft github_collector.cli.export_command.main auf; Logging und Konfiguration
werden vollständig im CLI-Modul über `config.LOG_CONFIG` gesteuert.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from github_collector.cli.export_command import main

if __name__ == "__main__":
    sys.exit(main())
