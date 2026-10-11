#!/usr/bin/env python3
"""Hilfsskript zum Zurücksetzen der Datenbank und Ausführen der Repository-Sammlung.

Dieses Skript implementiert eine erweiterte Logik zum Zurücksetzen verschiedener
Datenbanktypen (SQLite und MySQL). Für MySQL wird die Datenbank gelöscht (DROP DATABASE)
und neu erstellt (CREATE DATABASE). Anschließend wird die Funktion `init_db` aus
`github_collector.database.database` verwendet, um das Datenbankschema zu initialisieren.
Danach wird die Repository-Sammlung über `collect_repositories.py` gestartet.

Der Reset muss mit `--yes` bestätigt werden; für MySQL ist zusätzlich
`GITHUB_COLLECTOR_ALLOW_MYSQL_RESET=true` erforderlich.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import sqlalchemy
from sqlalchemy import text
from sqlalchemy.engine.url import make_url

from github_collector import config
from github_collector.database.database import init_db

# reset_command.main wird nicht verwendet, da dieses Skript eine eigene, erweiterte Reset-Logik hat.
# from github_collector.cli.reset_command import main as reset_command_main
from github_collector.utils.logging_config import setup_logging

logger = setup_logging(log_file=config.LOG_CONFIG["files"]["reset"])

DRY_RUN = os.getenv("GITHUB_COLLECTOR_DRY_RUN", "false").lower() in {"1", "true", "yes"}


def reset_database():
    """Setzt die Datenbank zurück, je nach Typ (SQLite, MySQL, ...)."""
    db_url = str(config.DB_URL)

    url = make_url(db_url)

    if url.drivername.startswith("sqlite"):
        db_file_path = url.database
        if DRY_RUN:
            logger.info(
                "[DRY-RUN] Würde SQLite-Datenbank-Datei löschen und Schema neu anlegen: %s",
                db_file_path,
            )
            return True

        if os.path.exists(db_file_path):
            os.remove(db_file_path)
        init_db(db_url)
        logger.info("SQLite-Datenbank zurückgesetzt.")
        return True

    elif url.drivername.startswith("mysql"):
        allow_mysql_reset = os.getenv("GITHUB_COLLECTOR_ALLOW_MYSQL_RESET", "false").lower() in {
            "1",
            "true",
            "yes",
        }

        if not allow_mysql_reset:
            logger.error(
                "MySQL-Reset ist deaktiviert. Setze GITHUB_COLLECTOR_ALLOW_MYSQL_RESET=true, "
                "wenn du das Zurücksetzen der MySQL-Datenbank explizit erlauben möchtest."
            )
            return False

        if DRY_RUN:
            logger.warning(
                "[DRY-RUN] Würde MySQL-Datenbank zurücksetzen (DROP/CREATE DATABASE): %s",
                url.database,
            )
            return True

        tmp_url = url.set(database=None)
        engine = sqlalchemy.create_engine(tmp_url)
        db_name = url.database
        logger.warning("MySQL-Datenbank wird zurückgesetzt (DROP/CREATE DATABASE): %s", db_name)
        with engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS `{db_name}`"))
            conn.execute(text(f"CREATE DATABASE `{db_name}` DEFAULT CHARACTER SET utf8mb4"))
        engine = sqlalchemy.create_engine(db_url)
        init_db(db_url)
        logger.info("MySQL-Datenbank zurückgesetzt.")
        return True

    else:
        logger.error(f"Reset für Datenbanktyp {url.drivername} nicht implementiert.")
        return False


def run_collect_repositories():
    """
    Führt das collect_repositories.py-Skript aus.

    Returns:
        True, wenn das Skript erfolgreich ausgeführt wurde, sonst False
    """
    project_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    collect_script = os.path.join(project_dir, "scripts", "collection", "collect_repositories.py")
    db_url = str(config.DB_URL)

    # Stelle sicher, dass das Skript existiert
    if not os.path.exists(collect_script):
        logger.error(f"Sammlung-Skript nicht gefunden: {collect_script}")
        return False

    # Führe das Skript aus
    cmd = [sys.executable, collect_script]

    # Wenn die zentrale DB-URL auf eine SQLite-Datei zeigt, Pfad explizit übergeben
    url = make_url(db_url)
    if url.drivername.startswith("sqlite") and url.database:
        cmd += ["--db-path", url.database]
    logger.info(f"Führe aus: {' '.join(cmd)}")

    if DRY_RUN:
        logger.info("[DRY-RUN] Würde Repository-Sammlung ausführen: %s", " ".join(cmd))
        return True

    try:
        env = os.environ.copy()
        subprocess.run(cmd, check=True, env=env)
        logger.info("Repository-Sammlung erfolgreich abgeschlossen")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Fehler bei der Repository-Sammlung: {e}")
        return False


def parse_arguments(argv=None):
    """Wertet die Kommandozeile aus und bricht ab, wenn der Reset nicht bestätigt wurde."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Bestätigt das Löschen der konfigurierten Datenbank. Ohne dieses Flag wird "
            "nichts gelöscht (Ausnahme: GITHUB_COLLECTOR_DRY_RUN=true, das nur protokolliert)."
        ),
    )
    args = parser.parse_args(argv)

    if not args.yes and not DRY_RUN:
        parser.error(
            "Dieses Skript löscht die konfigurierte Datenbank. Zum Bestätigen --yes angeben "
            "oder mit GITHUB_COLLECTOR_DRY_RUN=true nur anzeigen lassen, was passieren würde."
        )
    return args


def main(argv=None):
    """Hauptfunktion."""
    # Vor jeder anderen Aktion: --help, unbekannte Argumente und ein fehlendes --yes
    # beenden das Skript, bevor der Reset beginnt.
    parse_arguments(argv)

    # Datenbank zurücksetzen
    if not reset_database():
        logger.error("Datenbank-Reset fehlgeschlagen, breche ab")
        return 1

    # Repository-Sammlung ausführen
    if not run_collect_repositories():
        logger.error("Repository-Sammlung fehlgeschlagen")
        return 1

    logger.info("Datenbank-Reset und Repository-Sammlung erfolgreich abgeschlossen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
