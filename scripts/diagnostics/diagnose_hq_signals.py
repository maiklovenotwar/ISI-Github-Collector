#!/usr/bin/env python3
"""
Diagnostische Analyse der HQ-Signale für Multi-Hit Organizations.
"""

import sys
from pathlib import Path

# UTF-8 Output für Windows
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from github_collector.enrichment.orbis_client import OrbisClient  # noqa: E402


def search_ultimate_owner_tables(client: OrbisClient):  # noqa: C901
    """Sucht systematisch nach Ultimate Owner Tabellen und Feldern."""
    print("🔍 SYSTEMATISCHE SUCHE NACH ULTIMATE OWNER DATEN")
    print("=" * 70)

    cursor = client.connection.cursor()

    # 1. Tabellen mit GUO/UO/Owner/Independence Keywords
    print("📋 TABELLEN MIT OWNERSHIP-KEYWORDS:")
    print("-" * 50)

    query_tables = """
        SELECT TABLE_NAME FROM ALL_TABLES
        WHERE OWNER = 'ORBIS_23J'
        AND (
            TABLE_NAME LIKE '%GUO%'
            OR TABLE_NAME LIKE '%UO%'
            OR TABLE_NAME LIKE '%OWNER%'
            OR TABLE_NAME LIKE '%INDEP%'
            OR TABLE_NAME LIKE '%SHAREHOLD%'
            OR TABLE_NAME LIKE '%SUBSIDIARY%'
        )
        ORDER BY TABLE_NAME
    """

    cursor.execute(query_tables)
    tables = cursor.fetchall()

    if tables:
        for table in tables:
            print(f"  • {table[0]}")
    else:
        print("  ❌ Keine Tabellen mit GUO/UO/Owner Keywords gefunden")

    # 2. CONTACT_INFO Ownership Felder
    print("\n📊 OWNERSHIP-FELDER IN CONTACT_INFO:")
    print("-" * 50)

    query_contact_fields = """
        SELECT COLUMN_NAME, DATA_TYPE FROM ALL_TAB_COLUMNS
        WHERE OWNER = 'ORBIS_23J'
        AND TABLE_NAME = 'CONTACT_INFO'
        AND (
            COLUMN_NAME LIKE '%GUO%'
            OR COLUMN_NAME LIKE '%UO%'
            OR COLUMN_NAME LIKE '%INDEP%'
            OR COLUMN_NAME LIKE '%OWNER%'
        )
        ORDER BY COLUMN_NAME
    """

    cursor.execute(query_contact_fields)
    contact_fields = cursor.fetchall()

    if contact_fields:
        for field in contact_fields:
            print(f"  • {field[0]} ({field[1]})")
    else:
        print("  ❌ Keine GUO/Ownership Felder in CONTACT_INFO gefunden")

    # 3. Independence Indicator spezifische Suche
    print("\n🎯 INDEPENDENCE INDICATOR SUCHE:")
    print("-" * 50)

    query_indep_fields = """
        SELECT COLUMN_NAME, TABLE_NAME FROM ALL_TAB_COLUMNS
        WHERE OWNER = 'ORBIS_23J'
        AND COLUMN_NAME LIKE '%INDEP%'
        ORDER BY TABLE_NAME, COLUMN_NAME
    """

    cursor.execute(query_indep_fields)
    indep_fields = cursor.fetchall()

    if indep_fields:
        print("Independence Felder gefunden:")
        for field in indep_fields:
            print(f"  • {field[1]}.{field[0]}")
    else:
        print("  ❌ Keine Independence Indicator Felder gefunden")

    # 4. Analyse der ersten gefundenen Tabelle (falls vorhanden)
    if tables:
        table_name = tables[0][0]
        print(f"\n📈 ANALYSE VON {table_name}:")
        print("-" * 50)

        # Schema
        schema_query = """
            SELECT COLUMN_NAME, DATA_TYPE, NULLABLE
            FROM ALL_TAB_COLUMNS
            WHERE OWNER = 'ORBIS_23J'
            AND TABLE_NAME = :table_name
            ORDER BY COLUMN_ID
        """

        cursor.execute(schema_query, {"table_name": table_name})
        columns = cursor.fetchall()

        print("Schema (erste 10 Spalten):")
        for col in columns[:10]:
            nullable = "NULL" if col[2] == "Y" else "NOT NULL"
            print(f"  • {col[0]} {col[1]} {nullable}")

        if len(columns) > 10:
            print(f"  ... und {len(columns) - 10} weitere Spalten")

        # Zeilenzahl
        try:
            count_query = f"SELECT COUNT(*) FROM ORBIS_23J.{table_name}"
            cursor.execute(count_query)
            row_count = cursor.fetchone()[0]
            print(f"\nZeilenzahl: {row_count:,}")
        except Exception as e:
            print(f"\nZeilenzahl: Fehler - {e}")

    return {"tables": tables, "contact_fields": contact_fields, "indep_fields": indep_fields}


def diagnose_hq_signals(client: OrbisClient):
    """Analysiert Verfügbarkeit der HQ-Signale."""
    print("🔍 DIAGNOSTISCHE ANALYSE: HQ-SIGNAL VERFÜGBARKEIT")
    print("=" * 70)

    cursor = client.connection.cursor()

    # Multi-Hit Organizations identifizieren
    cursor.execute(
        """
        WITH multi_hit_orgs AS (
            SELECT DISTINCT g.GITHUB_ID, g.LOGIN
            FROM GITHUB_COMBINED_INPUT g
            JOIN ORBIS_23J.CONTACT_INFO c ON LOWER(c.NAME_NATIVE) = g.MATCH_VALUE
            WHERE g.MATCH_TYPE IN ('name_blog', 'name_only')
              AND g.GITHUB_ID IN (
                  SELECT g2.GITHUB_ID
                  FROM GITHUB_COMBINED_INPUT g2
                  JOIN ORBIS_23J.CONTACT_INFO c2 ON LOWER(c2.NAME_NATIVE) = g2.MATCH_VALUE
                  WHERE g2.MATCH_TYPE IN ('name_blog', 'name_only')
                  GROUP BY g2.GITHUB_ID
                  HAVING COUNT(*) > 1
              )
        ),
        signal_availability AS (
            SELECT
                m.GITHUB_ID,
                m.LOGIN,
                c.BVDID,
                c.NAME_NATIVE,
                -- Signal 1: HQ-Tabelle (nicht verfügbar)
                0 as HAS_HQ_ENTRY,
                -- Signal 2: Gründungsdatum
                CASE WHEN l.DATE_INC IS NOT NULL THEN 1 ELSE 0 END as HAS_FOUNDING_DATE,
                l.DATE_INC,
                -- Signal 3: Umsatz
                CASE WHEN f.OPERATING_REVENUE_TURNOVER IS NOT NULL THEN 1 ELSE 0 END as HAS_REVENUE,
                f.OPERATING_REVENUE_TURNOVER,
                -- Signal 4: Parent-Status
                CASE WHEN EXISTS (
                    SELECT 1 FROM ORBIS_23J.ALL_SUBSIDIARIES_L1 s
                    WHERE s.BVDID = c.BVDID
                ) THEN 1 ELSE 0 END as IS_PARENT
            FROM multi_hit_orgs m
            JOIN ORBIS_23J.CONTACT_INFO c ON LOWER(c.NAME_NATIVE) = (
                SELECT g.MATCH_VALUE
                FROM GITHUB_COMBINED_INPUT g
                WHERE g.GITHUB_ID = m.GITHUB_ID
                  AND g.MATCH_TYPE IN ('name_blog', 'name_only')
                  AND ROWNUM = 1
            )
            LEFT JOIN ORBIS_23J.LEGAL_INFO l ON l.BVDID = c.BVDID
            LEFT JOIN ORBIS_23J.KEY_FINANCIALS_EUR f ON f.BVDID = c.BVDID
        )
        SELECT
            'HQ_TABLE' as SIGNAL_TYPE,
            SUM(HAS_HQ_ENTRY) as AVAILABLE_COUNT,
            COUNT(*) as TOTAL_ORGS,
            ROUND(SUM(HAS_HQ_ENTRY) * 100.0 / COUNT(*), 1) as PERCENTAGE
        FROM signal_availability
        UNION ALL
        SELECT
            'FOUNDING_DATE' as SIGNAL_TYPE,
            SUM(HAS_FOUNDING_DATE) as AVAILABLE_COUNT,
            COUNT(*) as TOTAL_ORGS,
            ROUND(SUM(HAS_FOUNDING_DATE) * 100.0 / COUNT(*), 1) as PERCENTAGE
        FROM signal_availability
        UNION ALL
        SELECT
            'REVENUE' as SIGNAL_TYPE,
            SUM(HAS_REVENUE) as AVAILABLE_COUNT,
            COUNT(*) as TOTAL_ORGS,
            ROUND(SUM(HAS_REVENUE) * 100.0 / COUNT(*), 1) as PERCENTAGE
        FROM signal_availability
        UNION ALL
        SELECT
            'IS_PARENT' as SIGNAL_TYPE,
            SUM(IS_PARENT) as AVAILABLE_COUNT,
            COUNT(*) as TOTAL_ORGS,
            ROUND(SUM(IS_PARENT) * 100.0 / COUNT(*), 1) as PERCENTAGE
        FROM signal_availability
        ORDER BY AVAILABLE_COUNT DESC
    """
    )

    results = cursor.fetchall()

    print("Signal-Typ       | Verfügbar | Gesamt | Anteil")
    print("-----------------|-----------|--------|-------")

    for signal_type, available, total, percentage in results:
        print(f"{signal_type:15s} | {available:8,d} | {total:5,d} | {percentage:5.1f}%")

    print("-----------------|-----------|--------|-------")

    # Detailanalyse: Warum wurde HQ-Tabelle/Revenue nicht genutzt?
    print("\n📊 DETAILANALYSE:")
    print("-" * 30)

    cursor.execute(
        """
        WITH multi_hit_matches AS (
            SELECT
                g.GITHUB_ID,
                g.LOGIN,
                c.BVDID,
                c.NAME_NATIVE,
                l.DATE_INC,
                f.OPERATING_REVENUE_TURNOVER,
                CASE WHEN EXISTS (
                    SELECT 1 FROM ORBIS_23J.ALL_SUBSIDIARIES_L1 s
                    WHERE s.BVDID = c.BVDID
                ) THEN 1 ELSE 0 END as IS_PARENT
            FROM GITHUB_COMBINED_INPUT g
            JOIN ORBIS_23J.CONTACT_INFO c ON LOWER(c.NAME_NATIVE) = g.MATCH_VALUE
            LEFT JOIN ORBIS_23J.LEGAL_INFO l ON l.BVDID = c.BVDID
            LEFT JOIN ORBIS_23J.KEY_FINANCIALS_EUR f ON f.BVDID = c.BVDID
            WHERE g.MATCH_TYPE IN ('name_blog', 'name_only')
              AND g.GITHUB_ID IN (
                  SELECT g2.GITHUB_ID
                  FROM GITHUB_COMBINED_INPUT g2
                  JOIN ORBIS_23J.CONTACT_INFO c2 ON LOWER(c2.NAME_NATIVE) = g2.MATCH_VALUE
                  WHERE g2.MATCH_TYPE IN ('name_blog', 'name_only')
                  GROUP BY g2.GITHUB_ID
                  HAVING COUNT(*) > 1
              )
        )
        SELECT
            COUNT(DISTINCT GITHUB_ID) as UNIQUE_ORGS,
            COUNT(*) as TOTAL_MATCHES,
            SUM(CASE WHEN DATE_INC IS NOT NULL THEN 1 ELSE 0 END) as MATCHES_WITH_FOUNDING,
            SUM(CASE WHEN OPERATING_REVENUE_TURNOVER IS NOT NULL THEN 1 ELSE 0 END) as MATCHES_WITH_REVENUE,
            SUM(IS_PARENT) as MATCHES_IS_PARENT
        FROM multi_hit_matches
    """
    )

    org_count, match_count, founding_matches, revenue_matches, parent_matches = cursor.fetchone()

    print(f"Multi-Hit Organizations: {org_count:,}")
    print(f"Gesamt Matches: {match_count:,}")
    print(
        f"Matches mit Gründungsdatum: {founding_matches:,} ({founding_matches/match_count*100:.1f}%)"
    )
    print(f"Matches mit Umsatzdaten: {revenue_matches:,} ({revenue_matches/match_count*100:.1f}%)")
    print(f"Matches als Parent: {parent_matches:,} ({parent_matches/match_count*100:.1f}%)")

    return {
        "signal_availability": results,
        "match_stats": {
            "unique_orgs": org_count,
            "total_matches": match_count,
            "founding_matches": founding_matches,
            "revenue_matches": revenue_matches,
            "parent_matches": parent_matches,
        },
    }


def execute_ownership_analysis(client: OrbisClient):
    """Führt die vier Ownership-Queries direkt aus."""
    print("OWNERSHIP-TABELLEN ANALYSE - DIREKTE AUSFÜHRUNG")
    print("=" * 60)

    cursor = client.connection.cursor()

    # Query 1: Schema
    print("\nQUERY 1: SCHEMA DER DREI TABELLEN")
    print("-" * 40)

    query1 = """
    SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
    FROM ALL_TAB_COLUMNS
    WHERE OWNER = 'ORBIS_23J'
    AND TABLE_NAME IN (
        'ALL_CURR_SHAREHOLDERS_L1',
        'BASIC_SHAREHOLDER_INFO',
        'CONTROLLING_SHAREHOLDERS'
    )
    ORDER BY TABLE_NAME, COLUMN_ID
    """

    cursor.execute(query1)
    results1 = cursor.fetchall()

    for table, column, data_type in results1:
        print(f"{table} | {column} | {data_type}")

    # Query 2: Zeilenzahlen
    print("\nQUERY 2: ZEILENZAHLEN")
    print("-" * 40)

    query2 = """
    SELECT 'ALL_CURR_SHAREHOLDERS_L1' AS TBL, COUNT(*) AS CNT
    FROM ORBIS_23J.ALL_CURR_SHAREHOLDERS_L1
    UNION ALL
    SELECT 'BASIC_SHAREHOLDER_INFO', COUNT(*)
    FROM ORBIS_23J.BASIC_SHAREHOLDER_INFO
    UNION ALL
    SELECT 'CONTROLLING_SHAREHOLDERS', COUNT(*)
    FROM ORBIS_23J.CONTROLLING_SHAREHOLDERS
    """

    cursor.execute(query2)
    results2 = cursor.fetchall()

    for table, count in results2:
        print(f"{table} | {count:,}")

    # Query 3: Beispieldaten BASIC_SHAREHOLDER_INFO
    print("\nQUERY 3A: BASIC_SHAREHOLDER_INFO (3 Zeilen)")
    print("-" * 40)

    query3a = "SELECT * FROM ORBIS_23J.BASIC_SHAREHOLDER_INFO WHERE ROWNUM <= 3"
    cursor.execute(query3a)
    results3a = cursor.fetchall()

    # Spaltennamen für BASIC_SHAREHOLDER_INFO
    cursor.execute(
        "SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER = 'ORBIS_23J' AND TABLE_NAME = 'BASIC_SHAREHOLDER_INFO' ORDER BY COLUMN_ID"
    )
    columns3a = [row[0] for row in cursor.fetchall()]

    print(" | ".join(columns3a))
    for row in results3a:
        print(" | ".join(str(val) if val is not None else "NULL" for val in row))

    # Query 3: Beispieldaten CONTROLLING_SHAREHOLDERS
    print("\nQUERY 3B: CONTROLLING_SHAREHOLDERS (3 Zeilen)")
    print("-" * 40)

    query3b = "SELECT * FROM ORBIS_23J.CONTROLLING_SHAREHOLDERS WHERE ROWNUM <= 3"
    cursor.execute(query3b)
    results3b = cursor.fetchall()

    # Spaltennamen für CONTROLLING_SHAREHOLDERS
    cursor.execute(
        "SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER = 'ORBIS_23J' AND TABLE_NAME = 'CONTROLLING_SHAREHOLDERS' ORDER BY COLUMN_ID"
    )
    columns3b = [row[0] for row in cursor.fetchall()]

    print(" | ".join(columns3b))
    for row in results3b:
        print(" | ".join(str(val) if val is not None else "NULL" for val in row))

    # Query 4: Coverage
    print("\nQUERY 4: COVERAGE GEGEN GITHUB-ORBIS MATCHES")
    print("-" * 40)

    query4 = """
    SELECT
        COUNT(DISTINCT r.bvdid) AS total_matched,
        COUNT(DISTINCT b.BVDID) AS with_basic_info,
        COUNT(DISTINCT c.BVDID) AS with_controlling_shareholder
    FROM GITHUB_COMBINED_RESULTS r
    LEFT JOIN ORBIS_23J.BASIC_SHAREHOLDER_INFO b ON r.bvdid = b.BVDID
    LEFT JOIN ORBIS_23J.CONTROLLING_SHAREHOLDERS c ON r.bvdid = c.BVDID
    """

    cursor.execute(query4)
    results4 = cursor.fetchone()

    total, basic, controlling = results4
    print(f"Total Matched BVDIDs: {total:,}")
    print(f"With Basic Shareholder Info: {basic:,}")
    print(f"With Controlling Shareholders: {controlling:,}")

    if total > 0:
        print(f"Basic Info Coverage: {basic/total*100:.1f}%")
        print(f"Controlling Shareholder Coverage: {controlling/total*100:.1f}%")


def main():
    """Hauptfunktion."""
    try:
        client = OrbisClient()
        client._ensure_connection()

        # Direkte Ausführung der vier Ownership-Queries
        execute_ownership_analysis(client)

    except Exception as e:
        print(f"Fehler bei der Ownership-Analyse: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
