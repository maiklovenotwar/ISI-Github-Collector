"""CLI-Modul für den Export von Datenbanktabellen in CSV-Dateien.

Dieses Modul implementiert die Kommandozeilenschnittstelle für den Export
von Datenbanktabellen in CSV-Dateien.
"""

import argparse
import os
import sys

from github_collector import config
from github_collector.database.database import GitHubDatabase
from github_collector.export.csv_export import export_tables
from github_collector.utils.logging_config import get_export_logger

# Konfiguriere Logging
logger = get_export_logger()


def parse_arguments() -> argparse.Namespace:
    """
    Parse Kommandozeilenargumente.

    Returns:
        Geparste Argumente
    """
    parser = argparse.ArgumentParser(description="Exportiere Datenbanktabellen in CSV-Dateien")

    # Datenbankoptionen
    parser.add_argument("--db-path", help="Pfad zur SQLite-Datenbankdatei")

    # Exportoptionen
    parser.add_argument(
        "--output-dir",
        default=str(config.EXPORT_CONFIG["directory"]),
        help="Verzeichnis für die exportierten CSV-Dateien (Standard: exports)",
    )
    parser.add_argument(
        "--tables",
        nargs="+",
        choices=["contributors", "organizations", "repositories", "all"],
        default=["all"],
        help="Zu exportierende Tabellen (Standard: all)",
    )
    parser.add_argument(
        "--limit", type=int, help="Maximale Anzahl zu exportierender Zeilen pro Tabelle"
    )
    parser.add_argument(
        "--with-timestamp", action="store_true", help="Zeitstempel zum Dateinamen hinzufügen"
    )

    return parser.parse_args()


def main() -> int:
    """
    Hauptfunktion für den Export von Datenbanktabellen.

    Returns:
        Exit-Code (0 bei Erfolg, 1 bei Fehler)
    """
    args = parse_arguments()

    try:
        # Datenbank-URL bestimmen
        db_url = args.db_path or config.DB_URL

        # Falls ein expliziter Pfad übergeben wurde, der keine SQLAlchemy-URL ist,
        # baue eine SQLite-URL daraus
        if args.db_path and isinstance(db_url, str) and "://" not in db_url:
            if not os.path.isabs(db_url):
                project_root = os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
                db_url = os.path.join(project_root, db_url)
            db_url = f"sqlite:///{db_url}"

        logger.info(f"Verwende Datenbank-URL: {db_url}")

        # Initialisiere Datenbank
        db = GitHubDatabase(str(db_url))

        # Bestimme die zu exportierenden Tabellen
        tables = []
        if "all" in args.tables:
            tables = ["contributors", "organizations", "repositories"]
        else:
            tables = args.tables

        # Exportiere die Tabellen; eine fehlgeschlagene Tabelle ergibt Exit-Code 1
        _exported, failed = export_tables(
            db=db,
            tables=tables,
            output_dir=args.output_dir,
            limit=args.limit,
            with_timestamp=args.with_timestamp,
        )

        return 1 if failed else 0

    except Exception as e:
        logger.exception(f"Fehler beim Export der Datenbanktabellen: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
