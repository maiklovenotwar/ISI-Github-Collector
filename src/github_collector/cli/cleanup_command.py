"""
CLI-Modul zur Bereinigung verwaister Owner-Einträge.

Dieses Modul implementiert nur die Kommandozeilenschnittstelle und delegiert die
fachliche Logik an ``github_collector.owners.cleanup``.
"""

import argparse
import sys
from typing import Tuple

from github_collector import config
from github_collector.database.database import GitHubDatabase
from github_collector.owners.cleanup import (
    cleanup_orphaned_owners as core_cleanup_orphaned_owners,
)
from github_collector.utils.logging_config import setup_logging

# Konfiguriere Logging über zentrales LOG_CONFIG
logger = setup_logging(log_file=config.LOG_CONFIG["files"]["cleanup"])


def parse_arguments() -> argparse.Namespace:
    """
    Parse Kommandozeilenargumente.

    Returns:
        Geparste Argumente
    """
    parser = argparse.ArgumentParser(
        description="Bereinige verwaiste Owner-Einträge (Contributors und Organisationen)"
    )

    # Datenbankoptionen
    parser.add_argument("--db-path", help="Pfad zur SQLite-Datenbankdatei")

    # Bereinigungsoptionen
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Simulationsmodus: Zeigt an, welche Einträge entfernt würden, "
            "ohne Änderungen vorzunehmen"
        ),
    )

    return parser.parse_args()


def cleanup_orphaned_owners(db: GitHubDatabase, dry_run: bool = False) -> Tuple[int, int]:
    """Wrapper für die zentrale Cleanup-Funktion.

    Diese Funktion existiert für Rückwärtskompatibilität und um eine klare
    Signatur im CLI-Modul zu behalten. Die eigentliche Logik liegt in
    ``github_collector.owners.cleanup.cleanup_orphaned_owners``.
    """
    return core_cleanup_orphaned_owners(db, dry_run=dry_run)


def main() -> int:
    """
    Hauptfunktion zur Bereinigung verwaister Owner-Einträge.

    Returns:
        Exit-Code (0 bei Erfolg, 1 bei Fehler)
    """
    args = parse_arguments()

    try:
        # Datenbankverbindung initialisieren
        db_url = args.db_path or config.DB_URL
        # Falls ein expliziter Pfad übergeben wurde, der keine SQLAlchemy-URL ist, baue eine daraus
        if args.db_path and isinstance(db_url, str) and "://" not in db_url:
            db_url = f"sqlite:///{db_url}"

        # Initialisiere Datenbank
        db = GitHubDatabase(str(db_url))

        # Bereinige verwaiste Owner-Einträge
        removed_contributors, removed_organizations = cleanup_orphaned_owners(
            db, dry_run=args.dry_run
        )

        # Schließe Datenbankverbindung
        db.close()

        # Ausgabe der Ergebnisse
        if args.dry_run:
            logger.info(
                "Simulationsmodus: %d Contributors und %d Organisationen " "würden entfernt werden",
                removed_contributors,
                removed_organizations,
            )
        else:
            logger.info(
                "Bereinigung abgeschlossen: %d Contributors und %d " "Organisationen entfernt",
                removed_contributors,
                removed_organizations,
            )

        return 0

    except Exception as e:
        logger.exception(f"Fehler bei der Bereinigung verwaister Owner-Einträge: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
