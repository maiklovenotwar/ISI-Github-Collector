#!/usr/bin/env python3
"""Zählt Orbis-Firmennamen ohne lateinische Buchstaben (Befund M1, Entscheidung E23).

Die Namensähnlichkeit des Orbis-Matchings vergleicht nur a-z und 0-9; Namen in anderer
Schrift werden nur über eine geschützte Umschrift verglichen. Dieses Skript misst, wie
viele `NAME_NATIVE` keinen lateinischen Buchstaben enthalten - insgesamt und für die 20
Länder mit den meisten solchen Namen - und ob `CONTACT_INFO` eine weitere Namensspalte
führt, die einen lateinischen Namen enthalten könnte.

Nur lesend, braucht VPN. Ausgegeben werden nur Zahlen und Ländercodes, keine Firmennamen
und keine Spaltennamen (Lizenz, siehe docs/methodology/orbis_enrichment.md).

Usage:
    python scripts/diagnostics/count_orbis_non_latin_names.py
"""

import sys
from pathlib import Path
from typing import Dict

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from github_collector.enrichment.orbis_client import OrbisClient  # noqa: E402

_WITHOUT_LATIN = "SUM(CASE WHEN NOT REGEXP_LIKE(NAME_NATIVE, '[A-Za-z]') THEN 1 ELSE 0 END)"


def build_queries(schema: str) -> Dict[str, str]:
    """Die drei lesenden Abfragen; `schema` ist das Orbis-Schema (z. B. ORBIS_23J)."""
    return {
        "non_latin_total": f"""
            SELECT COUNT(NAME_NATIVE) AS with_name, {_WITHOUT_LATIN} AS without_latin
            FROM {schema}.CONTACT_INFO
            WHERE NAME_NATIVE IS NOT NULL""",
        "non_latin_by_country": f"""
            SELECT * FROM (
                SELECT CTRYISO, COUNT(NAME_NATIVE) AS with_name,
                       {_WITHOUT_LATIN} AS without_latin
                FROM {schema}.CONTACT_INFO
                WHERE NAME_NATIVE IS NOT NULL
                GROUP BY CTRYISO
                ORDER BY without_latin DESC
            ) WHERE ROWNUM <= 20""",
        "latin_name_column_exists": f"""
            SELECT COUNT(*) AS other_name_columns
            FROM ALL_TAB_COLUMNS
            WHERE OWNER = '{schema}' AND TABLE_NAME = 'CONTACT_INFO'
              AND COLUMN_NAME LIKE '%NAME%' AND COLUMN_NAME <> 'NAME_NATIVE'""",
    }


def main() -> int:
    client = OrbisClient()
    try:
        schema = client.config.get("schema", "ORBIS_23J")
        client._ensure_connection()  # die Verbindung wird erst bei Bedarf aufgebaut
        cursor = client.connection.cursor()
        for key, query in build_queries(schema).items():
            cursor.execute(query)
            columns = [d[0].lower() for d in cursor.description]
            print(f"\n{key}: {', '.join(columns)}")
            for row in cursor.fetchall():
                print("  " + " | ".join(str(value) for value in row))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
