"""Zentrale Funktionen zur Bereinigung verwaister Owner-Einträge.

Dieses Modul kapselt die fachliche Logik, um Contributors und Organisationen
ohne zugehörige Repositories zu identifizieren und optional zu löschen.

Die Funktion `cleanup_orphaned_owners` arbeitet nur auf einer bestehenden
`GitHubDatabase`-Instanz und steuert selbst keinen Verbindungsaufbau oder
-abbau. Aufrufer (z.B. CLI-Module) sind verantwortlich für:

- Erstellen der `GitHubDatabase`
- Aufrufen dieser Funktion mit `dry_run=True/False`
- Schließen der Datenbankverbindung
"""

import logging
from typing import Tuple

from github_collector.database.database import GitHubDatabase

logger = logging.getLogger(__name__)


def cleanup_orphaned_owners(db: GitHubDatabase, dry_run: bool = False) -> Tuple[int, int]:
    """Entfernt Owner (Contributors/Organisationen) ohne zugehörige Repositories.

    Args:
        db: Geöffnete Datenbankverbindung.
        dry_run: Wenn True, werden keine Änderungen vorgenommen, sondern nur
            angezeigt, welche Einträge entfernt würden.

    Returns:
        Tuple mit der Anzahl der entfernten Contributors und Organisationen.
    """
    removed_contributors, removed_organizations = db.delete_orphaned_owners(dry_run=dry_run)

    logger.info(
        "Cleanup abgeschlossen: %d Contributors und %d Organisationen entfernt (dry_run=%s)",
        removed_contributors,
        removed_organizations,
        dry_run,
    )

    return removed_contributors, removed_organizations
