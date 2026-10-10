#!/usr/bin/env python3
"""
Prüft verfügbare ORBIS-Tabellen und deren Spalten für HQ-Resolution.
"""

import sys
from pathlib import Path

# UTF-8 Output für Windows
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from github_collector.enrichment.orbis_client import OrbisClient  # noqa: E402


def check_orbis_tables(client: OrbisClient):
    """Prüft ORBIS-Tabellen für HQ-Signale."""
    print("🔍 ORBIS HQ-SIGNAL TABELLEN-ANALYSE")
    print("=" * 60)

    cursor = client.connection.cursor()

    # Liste der benötigten Tabellen für HQ-Resolution
    hq_tables = ["HQ_INFO", "LEGAL_INFO", "KEY_FINANCIALS_EUR", "ALL_SUBSIDIARIES_L1"]

    for table in hq_tables:
        print(f"\n📋 Tabelle: ORBIS_23J.{table}")
        print("-" * 40)

        try:
            # Prüfe ob Tabelle existiert
            cursor.execute(f"SELECT COUNT(*) FROM ORBIS_23J.{table} WHERE ROWNUM <= 1")
            cursor.fetchone()
            print("✅ Tabelle existiert")

            # Zeige relevante Spalten
            if table == "LEGAL_INFO":
                cursor.execute(
                    f"""
                    SELECT COLUMN_NAME
                    FROM ALL_TAB_COLUMNS
                    WHERE OWNER = 'ORBIS_23J' AND TABLE_NAME = '{table}'
                      AND (COLUMN_NAME LIKE '%DATE%' OR COLUMN_NAME LIKE '%INCORPORATION%')
                    ORDER BY COLUMN_NAME
                """
                )
            elif table == "KEY_FINANCIALS_EUR":
                cursor.execute(
                    f"""
                    SELECT COLUMN_NAME
                    FROM ALL_TAB_COLUMNS
                    WHERE OWNER = 'ORBIS_23J' AND TABLE_NAME = '{table}'
                      AND (COLUMN_NAME LIKE '%REVENUE%' OR COLUMN_NAME LIKE '%TURNOVER%' OR COLUMN_NAME LIKE '%SALES%')
                    ORDER BY COLUMN_NAME
                """
                )
            else:
                cursor.execute(
                    f"""
                    SELECT COLUMN_NAME
                    FROM ALL_TAB_COLUMNS
                    WHERE OWNER = 'ORBIS_23J' AND TABLE_NAME = '{table}'
                    ORDER BY COLUMN_NAME
                """
                )

            columns = cursor.fetchall()
            if columns:
                print("Relevante Spalten:")
                for (col_name,) in columns:
                    print(f"  {col_name}")
            else:
                print("❌ Keine relevanten Spalten gefunden")

        except Exception as e:
            print(f"❌ Tabelle nicht verfügbar: {e}")


def main():
    """Hauptfunktion."""
    try:
        client = OrbisClient()
        client._ensure_connection()

        try:
            check_orbis_tables(client)
            return 0

        finally:
            client.close()

    except Exception as e:
        print(f"❌ Fehler: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
