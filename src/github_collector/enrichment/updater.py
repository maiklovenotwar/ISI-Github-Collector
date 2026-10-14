"""
Updater-Modul für das Aktualisieren der Repository-Statistiken in der Datenbank.
"""

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# contributors_count wird nicht erhoben (Performance-Grenze der API) und deshalb auch nicht
# geschrieben: früher stand dort für jedes Repository 0, was "keine Mitwirkenden" bedeutet
# hätte (Befund K4). Die Spalte bleibt NULL.
UPDATE_STATS = text(
    """
    UPDATE repositories
    SET commits_count = :commits_count,
        pull_requests_count = :pull_requests_count
    WHERE id = :id
"""
)


def _database_id(stat: Dict[str, Any]) -> Optional[int]:
    """Numerische Repository-ID aus einem Ergebnis-Dict, sonst None."""
    for key in ("databaseId", "database_id"):
        value = stat.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def map_and_update_stats(
    engine: Engine, repo_stats: List[Dict[str, Any]], dry_run: bool = False
) -> int:
    """Schreibt Commit- und Pull-Request-Zahlen in einer Transaktion.

    :param engine: Datenbank-Engine (wird vom Aufrufer wiederverwendet)
    :param repo_stats: Ergebnis-Dicts aus GraphQLHandler.fetch_repo_stats
    :param dry_run: Wenn True, wird nichts geschrieben
    :return: Anzahl der Repositories, die geschrieben wurden (bzw. würden)
    :raises: Datenbankfehler werden nicht verschluckt; der Aufrufer bricht ab.
    """
    rows = []
    for stat in repo_stats:
        repo_id = _database_id(stat)
        if repo_id is None:
            logger.warning("Repo-Stat ohne numerische ID übersprungen: %s", stat)
            continue
        rows.append(
            {
                "id": repo_id,
                "commits_count": stat.get("calculated_commit_count"),
                "pull_requests_count": stat.get("calculated_pr_count"),
            }
        )

    if dry_run:
        logger.info("DRY-RUN: %d Repositories würden aktualisiert.", len(rows))
        return len(rows)

    if rows:
        with engine.begin() as conn:
            conn.execute(UPDATE_STATS, rows)
    logger.info("%d Repositories aktualisiert.", len(rows))
    return len(rows)
