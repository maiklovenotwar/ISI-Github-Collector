#!/usr/bin/env python3
"""Legt Zeilen in `contributors` für bislang unbekannte Repository-Contributors an.

Die Contributor-Netzwerk-Sammlung (collect_repository_contributors.py)
hat pro Zeile eine stabile GitHub-User-ID (github_user_id), aber der
Contributors-Listen-Endpoint liefert kein location/company/name - dafür ist
ein Profil-Abruf pro User nötig, den scripts/enrichment/enrich_contributor_profiles.py
übernimmt, und zwar ausschließlich für Zeilen, die schon in `contributors`
existieren.

Dieses Skript schließt die Lücke: Es legt für jede in repository_contributors
gesehene, aber lokal unbekannte, nicht-anonyme, nicht-Bot GitHub-User-ID eine
minimale Zeile an (einen "Stub": nur id, login und type, alles andere NULL). Die
Profil-Anreicherung greift solche Zeilen anschließend auf.

Nach dem Anlegen werden die zugehörigen repository_contributors.contributor_id
per einmaligem UPDATE...JOIN nachgezogen (keine API-Calls nötig).

Schritt 2 von collect_contributor_network.py. Geschrieben wird mit MySQL-spezifischem SQL
(INSERT IGNORE, UPDATE ... JOIN); auf anderen Datenbanken läuft nur `--dry-run`.
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from sqlalchemy import text  # noqa: E402
from tqdm import tqdm  # noqa: E402

from github_collector import config  # noqa: E402
from github_collector.database.engine import make_engine  # noqa: E402

CHUNK_SIZE = 2000


def find_missing_contributors(engine) -> list:
    """GitHub-User-ID, Login und Typ aller gesehenen, aber noch nicht angelegten Accounts."""
    with engine.connect() as conn:
        return conn.execute(
            text(
                """
                SELECT rc.github_user_id, MIN(rc.github_login) AS login,
                       MIN(rc.github_type) AS type
                FROM repository_contributors rc
                LEFT JOIN contributors c ON c.id = rc.github_user_id
                WHERE rc.contributor_id IS NULL
                  AND rc.is_anonymous = 0
                  AND rc.github_user_id IS NOT NULL
                  AND (rc.github_type IS NULL OR rc.github_type != 'Bot')
                  AND c.id IS NULL
                GROUP BY rc.github_user_id
                """
            )
        ).fetchall()


def parse_arguments(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Lege fehlende Contributors aus repository_contributors in contributors an"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Nur zaehlen, ohne DB-Schreibvorgaenge"
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_arguments(argv)
    engine = make_engine(str(config.DB_URL))

    candidates = find_missing_contributors(engine)
    print(f"{len(candidates)} neue Contributor-Stubs zum Anlegen gefunden.")
    if not candidates:
        return 0
    if args.dry_run:
        print("[DRY-RUN] Keine Aenderungen geschrieben.")
        return 0
    if engine.dialect.name != "mysql":
        print(
            "Dieses Skript schreibt mit MySQL-spezifischem SQL und unterstuetzt nur MySQL "
            f"(konfiguriert: {engine.dialect.name}). Mit --dry-run wird nur gezaehlt.",
            file=sys.stderr,
        )
        return 2

    with tqdm(total=len(candidates), desc="Stubs") as pbar:
        for i in range(0, len(candidates), CHUNK_SIZE):
            chunk = candidates[i : i + CHUNK_SIZE]
            rows = [{"id": r[0], "login": r[1], "type": r[2]} for r in chunk]
            stmt = text(
                "INSERT IGNORE INTO contributors (id, login, type) " "VALUES (:id, :login, :type)"
            )
            with engine.begin() as conn:
                conn.execute(stmt, rows)
            pbar.update(len(chunk))

    print(f"{len(candidates)} Stubs versucht anzulegen (INSERT IGNORE, Duplikate uebersprungen).")

    print("Ziehe repository_contributors.contributor_id fuer neu angelegte Stubs nach...")
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE repository_contributors rc
                JOIN contributors c ON c.id = rc.github_user_id
                SET rc.contributor_id = c.id
                WHERE rc.contributor_id IS NULL AND rc.is_anonymous = 0
                """
            )
        )
        print(f"{result.rowcount} repository_contributors-Zeilen mit contributor_id verknuepft.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
