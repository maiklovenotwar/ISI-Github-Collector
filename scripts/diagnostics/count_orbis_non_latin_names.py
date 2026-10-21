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

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from github_collector.enrichment.orbis_client import OrbisClient  # noqa: E402

# Die Buchstaben einzeln statt als Bereich [A-Za-z]: ein Bereich hängt von NLS_SORT ab und
# kann bei sprachlicher Sortierung auch Buchstaben mit diakritischen Zeichen einschließen.
_LATIN_LETTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_WITHOUT_LATIN = (
    f"SUM(CASE WHEN NOT REGEXP_LIKE(NAME_NATIVE, '[{_LATIN_LETTERS}]') THEN 1 ELSE 0 END)"
)


def build_queries(schema: str) -> Dict[str, str]:
    """Die lesenden Abfragen; `schema` ist das Orbis-Schema (z. B. ORBIS_23J).

    Die Firmentabelle wird nur einmal gelesen: die Summe ergibt sich aus den Ländern.
    """
    return {
        "non_latin_by_country": f"""
            SELECT CTRYISO, COUNT(NAME_NATIVE) AS with_name, {_WITHOUT_LATIN} AS without_latin
            FROM {schema}.CONTACT_INFO
            WHERE NAME_NATIVE IS NOT NULL
            GROUP BY CTRYISO""",
        "latin_name_column_exists": f"""
            SELECT COUNT(*) AS other_name_columns
            FROM ALL_TAB_COLUMNS
            WHERE OWNER = '{schema}' AND TABLE_NAME = 'CONTACT_INFO'
              AND COLUMN_NAME LIKE '%NAME%' AND COLUMN_NAME <> 'NAME_NATIVE'""",
    }


def summarize(
    rows: List[Tuple[str, int, int]], top: int = 20
) -> Tuple[Tuple[int, int], List[Tuple[str, int, int]]]:
    """(Namen insgesamt, davon ohne lateinischen Buchstaben) und die `top` Länder mit den meisten."""
    total = (sum(row[1] for row in rows), sum(row[2] for row in rows))
    return total, sorted(rows, key=lambda row: row[2], reverse=True)[:top]


def main(argv: Optional[Sequence[str]] = None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    client = OrbisClient()
    try:
        schema = client.config.get("schema", "ORBIS_23J")
        client._ensure_connection()  # die Verbindung wird erst bei Bedarf aufgebaut
        # Die Zählungen lesen die ganze Tabelle; die 180 s des Clients reichen dafür nicht.
        client.connection.call_timeout = 30 * 60 * 1000
        cursor = client.connection.cursor()
        queries = build_queries(schema)
        cursor.execute(queries["non_latin_by_country"])
        (with_name, without_latin), top = summarize(cursor.fetchall())
        print(f"\nnon_latin_total: with_name, without_latin\n  {with_name} | {without_latin}")
        print("\nnon_latin_by_country: ctryiso, with_name, without_latin")
        for row in top:
            print("  " + " | ".join(str(value) for value in row))
        cursor.execute(queries["latin_name_column_exists"])
        print(f"\nlatin_name_column_exists: other_name_columns\n  {cursor.fetchone()[0]}")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
