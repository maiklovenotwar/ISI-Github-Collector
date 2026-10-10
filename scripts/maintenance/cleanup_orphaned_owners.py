#!/usr/bin/env python3
"""
Skript zur Bereinigung verwaister Owner-Einträge (Contributors und Organisationen).

Wrapper für die CLI-Funktionalität des GitHub Data Collectors.
"""
import sys

from github_collector.cli.cleanup_command import main

if __name__ == "__main__":
    sys.exit(main())
