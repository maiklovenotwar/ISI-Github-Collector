"""
Updater-Modul für das Aktualisieren der Repository-Statistiken in der SQLite-DB
unterstützt Dry-Run, Logging und Transaktionssicherheit.
"""

import logging
from typing import Any, Dict, List

from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def map_and_update_stats(  # noqa: C901
    db_url: str, repo_stats: List[Dict[str, Any]], dry_run: bool = False
) -> int:
    """Aktualisiert Repository-Statistiken in der Ziel-DB.

    :param db_path: Pfad zur SQLite-DB
    :param repo_stats: Liste von Dicts mit
        ``repo_id``, ``calculated_pr_count``, ``calculated_commit_count``,
        ``calculated_contributor_count``
    :param dry_run: Wenn True, werden keine Änderungen in die DB geschrieben
        (nur Logging und Simulation)
    :return: Anzahl der erfolgreich aktualisierten Repositories

    Besonderheiten:
    - Unterstützt Dry-Run für sichere Simulationen
    - Umfassende Fehlerbehandlung und Logging für Nachvollziehbarkeit
    - Gibt die Anzahl der erfolgreich aktualisierten Repositories zurück
    """
    logger.info(
        "Starte Repository-Update (dry_run=%s) für %d Repositories...",
        dry_run,
        len(repo_stats),
    )
    updated = 0
    not_found = []

    # Debug: Logge ALLE databaseId-Werte und deren Typen aus repo_stats
    all_dbids = [
        stat.get("databaseId") if stat.get("databaseId") is not None else stat.get("database_id")
        for stat in repo_stats
    ]
    logger.debug(f"Alle databaseId/database_id-Werte aus repo_stats: {all_dbids}")
    logger.debug(f"Typen der databaseId/database_id-Werte: {[type(x) for x in all_dbids]}")

    # Debug: Gib die ersten paar Elemente von repo_stats aus, um die Struktur zu sehen
    if repo_stats:
        logger.debug(f"Erstes Element in repo_stats: {repo_stats[0]}")

    try:
        engine = create_engine(db_url)
        with engine.begin() as conn:
            # Debug: Zeige alle IDs in der DB (nur beim ersten Durchlauf, für Übersicht)
            result = conn.execute(text("SELECT id FROM repositories LIMIT 10"))
            ids_in_db = [row[0] for row in result.fetchall()]
            logger.debug(
                "Beispiel-IDs in DB (erste 10): %s (Typen: %s)",
                ids_in_db,
                [type(x) for x in ids_in_db],
            )

            # Mapping: databaseId -> Werte
            update_tuples = []
            for stat in repo_stats:
                # Flexible ID-Erkennung: Unterstützt verschiedene mögliche
                # Schlüsselnamen für die Repo-ID
                id_keys = ["databaseId", "database_id", "repo_id", "id"]
                database_id_value = None
                for key in id_keys:
                    if key in stat and stat[key] is not None:
                        database_id_value = stat[key]
                        logger.debug(f"ID-Schlüssel gefunden: '{key}' mit Wert {database_id_value}")
                        break
                if database_id_value is None:
                    logger.warning(f"Kein gültiger Repo-ID-Schlüssel in Stat: {stat}")
                    continue
                # Logging vor Typkonvertierung
                logger.debug(
                    f"Vor int()-Cast: {database_id_value} (Typ: {type(database_id_value)})"
                )
                # Akzeptiere numerische Strings und Integers
                if isinstance(database_id_value, int):
                    pass  # alles ok
                elif isinstance(database_id_value, str):
                    # Akzeptiere auch Strings, die wie eine Zahl aussehen (z.B. "12345")
                    try:
                        database_id_value_int = int(database_id_value)
                        logger.debug(
                            f"String-ID erfolgreich zu int gecastet: {database_id_value_int}"
                        )
                        database_id_value = database_id_value_int
                    except Exception:
                        logger.warning(
                            "Repo-Stat mit nicht-numerischer ID (String) "
                            "übersprungen: %s | Stat: %s",
                            database_id_value,
                            stat,
                        )
                        continue
                else:
                    try:
                        database_id_value = int(database_id_value)
                    except Exception:
                        logger.warning(
                            "Repo-Stat mit nicht-numerischer ID (Typ %s) "
                            "übersprungen: %s | Stat: %s",
                            type(database_id_value),
                            database_id_value,
                            stat,
                        )
                        continue
                update_tuple = {
                    "contributors_count": stat.get("calculated_contributor_count"),
                    "commits_count": stat.get("calculated_commit_count"),
                    "pull_requests_count": stat.get("calculated_pr_count"),
                    "id": database_id_value,
                }
                update_tuples.append(update_tuple)

            # Bulk-Update
            updated = 0
            if update_tuples:
                if not dry_run:
                    for ut in update_tuples:
                        conn.execute(
                            text(
                                """
                                UPDATE repositories
                                SET contributors_count = :contributors_count,
                                    commits_count = :commits_count,
                                    pull_requests_count = :pull_requests_count
                                WHERE id = :id
                            """
                            ),
                            ut,
                        )
                        updated += 1
                    logger.info(
                        "%d Repositories erfolgreich aktualisiert.",
                        len(update_tuples),
                    )
                else:
                    logger.info(
                        "DRY-RUN: %d Repositories würden aktualisiert werden.",
                        len(update_tuples),
                    )
                    updated = len(update_tuples)

        if dry_run:
            # Die Zählung 'updated' zählt, wie viele gefunden wurden,
            # nicht wie viele wirklich geändert würden.
            logger.info(
                "[Dry-Run] %d Repositories WÜRDEN aktualisiert, wenn sie "
                "in der DB gefunden werden.",
                updated,
            )

        if not_found:
            logger.warning(
                "%d Repositories aus GraphQL-Antwort wurden nicht in der DB " "gefunden: %s...",
                len(not_found),
                not_found[:10],
            )  # Zeige die ersten paar

    except Exception as e:
        logger.error(f"Fehler beim Update-Prozess: {e}", exc_info=True)
        if (
            not dry_run
            and "conn" in locals()
            and hasattr(conn, "in_transaction")
            and conn.in_transaction
        ):
            try:
                conn.rollback()
                logger.info("Transaktion zurückgerollt.")
            except Exception as roll_e:
                logger.error(f"Fehler beim Rollback: {roll_e}")
        return 1  # Fehlercode
    finally:
        if "conn" in locals():
            conn.close()
            logger.debug("DB-Verbindung geschlossen.")

    logger.info("Update-Prozess abgeschlossen.")
    # Gib die Anzahl der tatsächlich durchgeführten Updates zurück (im
    # Nicht-DryRun) oder der gefundenen Matches (im DryRun)
    return len(update_tuples) if not dry_run else updated
