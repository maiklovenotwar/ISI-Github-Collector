#!/usr/bin/env python
"""Füge ccTLD-Spalten zur contributors-Tabelle hinzu (OHNE Index)."""
import sys
from pathlib import Path

# UTF-8 Output für Windows
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from sqlalchemy import create_engine, text  # noqa: E402

from github_collector.config import DB_URL  # noqa: E402


def main():
    print("Füge ccTLD-Spalten zur contributors-Tabelle hinzu (OHNE Index)...")
    print(f"Datenbank: {DB_URL.split('@')[-1] if '@' in DB_URL else DB_URL}")
    print()

    engine = create_engine(DB_URL)

    with engine.connect() as conn:
        # Prüfe ob Spalten bereits existieren
        result = conn.execute(
            text(
                """
            SELECT COUNT(*) as cnt
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
            AND TABLE_NAME = 'contributors'
            AND COLUMN_NAME = 'country_code_cctld'
        """
            )
        )

        exists = result.fetchone()[0] > 0

        if exists:
            print("✓ Spalten existieren bereits.")

            # Prüfe Index
            result = conn.execute(
                text(
                    """
                SELECT COUNT(*) as cnt
                FROM INFORMATION_SCHEMA.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                AND TABLE_NAME = 'contributors'
                AND INDEX_NAME = 'idx_contributors_cctld'
            """
                )
            )

            has_index = result.fetchone()[0] > 0
            if has_index:
                print("✓ Index existiert bereits.")
            else:
                print("⚠ Index fehlt (kann später hinzugefügt werden)")

            return 0

        print("Füge Spalten hinzu (das kann 2-5 Minuten dauern)...")

        # Spalten hinzufügen OHNE Index
        conn.execute(
            text(
                """
            ALTER TABLE contributors
            ADD COLUMN country_code_cctld VARCHAR(2) DEFAULT NULL,
            ADD COLUMN cctld_source VARCHAR(10) DEFAULT NULL,
            ADD COLUMN cctld_enriched_at DATETIME DEFAULT NULL,
            ADD COLUMN cctld_run_id VARCHAR(36) DEFAULT NULL
        """
            )
        )
        conn.commit()
        print("✓ Spalten hinzugefügt")

        # Statistik
        result = conn.execute(
            text(
                """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN country_code IS NOT NULL THEN 1 ELSE 0 END) as with_geocoding
            FROM contributors
        """
            )
        )

        row = result.fetchone()
        print()
        print("Statistik:")
        print(f"  Total Contributors: {row[0]:,}")
        print(f"  Mit Geocoding: {row[1]:,}")
        print()
        print("✓ Migration erfolgreich abgeschlossen")
        print()
        print("HINWEIS: Index wurde NICHT erstellt (für bessere Performance).")
        print("Du kannst das ccTLD-Enrichment jetzt ausführen.")
        print("Index kann später mit folgendem SQL hinzugefügt werden:")
        print("  CREATE INDEX idx_contributors_cctld ON contributors(country_code_cctld);")

    return 0


if __name__ == "__main__":
    sys.exit(main())
