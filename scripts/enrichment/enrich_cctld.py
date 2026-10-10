#!/usr/bin/env python
"""ccTLD-Enrichment: Country Code aus Blog/Email-TLD ableiten.

Leitet für GitHub-Organizations oder -Contributors ohne Country Code einen
ISO-Ländercode aus der ccTLD der Blog-URL oder Email-Adresse ab. Die
Extraktionslogik liegt in github_collector.enrichment.cctld; dieses Skript
wendet sie auf die gewählte Tabelle an.

Geschrieben werden ausschließlich die Spalten country_code_cctld,
cctld_source, cctld_enriched_at und cctld_run_id der gewählten Tabelle.

Usage:
    python scripts/enrichment/enrich_cctld.py                         # Dry-Run für Organizations (default)
    python scripts/enrichment/enrich_cctld.py --entity contributors   # Dry-Run für Contributors
    python scripts/enrichment/enrich_cctld.py --write                 # Live-Import
    python scripts/enrichment/enrich_cctld.py --limit 100             # Nur 100 Zeilen verarbeiten
    python scripts/enrichment/enrich_cctld.py --validate-only         # Nur Validierung, kein Import
"""
import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8")

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from sqlalchemy import create_engine, text

from github_collector.config import DB_URL
from github_collector.enrichment.cctld import (
    COMPOUND_TLD_MAP,
    FILTERED_TLDS,
    VALID_ISO_CODES,
    domain_to_country_code,
    extract_cctld,
    extract_domain_from_blog,
    extract_domain_from_email,
)


@dataclass(frozen=True)
class EntityConfig:
    """Tabellenspezifische Unterschiede zwischen Organizations und Contributors."""

    table: str
    label: str
    # Zusätzliche Ausschlussbedingung für die Ziel-Population des Enrichments
    extra_target_filter: str


ENTITIES = {
    # Organizations mit Orbis-Country-Code werden ausgelassen: Orbis gilt als
    # Ground-Truth und hat Vorrang vor der ccTLD-Ableitung.
    "organizations": EntityConfig(
        table="organizations",
        label="Organizations",
        extra_target_filter="AND (country_code_orbis IS NULL OR country_code_orbis = '')",
    ),
    # contributors hat keine Orbis-Spalten.
    "contributors": EntityConfig(
        table="contributors",
        label="Contributors",
        extra_target_filter="",
    ),
}


# ============================================================
# VALIDIERUNG
# ============================================================


def run_validation(conn, entity: EntityConfig) -> dict:  # noqa: C901
    """Validiere ccTLD-Extraktion gegen Geocoding-Population.

    Prüft alle Zeilen der Tabelle, die sowohl Geocoding-CC als auch Blog/Email haben.
    Gibt Agree-Rate und Disagree-Analyse zurück.
    """
    print("\n" + "=" * 70)
    print(f"VALIDIERUNG: ccTLD vs. Geocoding ({entity.label})")
    print("=" * 70)

    rows = conn.execute(
        text(
            f"""
        SELECT id, blog, email, country_code
        FROM {entity.table}
        WHERE country_code IS NOT NULL AND country_code != ''
        AND ((blog IS NOT NULL AND blog != '' AND blog LIKE '%.%')
             OR (email IS NOT NULL AND email != ''
                 AND email NOT LIKE '%@users.noreply.github.com'))
    """
        )
    ).fetchall()

    total = len(rows)
    print(f"Validierungs-Population: {total:,} {entity.label}")

    agree = 0
    disagree = 0
    no_tld = 0
    disagree_pairs = Counter()
    agree_by_source = Counter()
    disagree_by_source = Counter()
    total_by_source = Counter()
    blog_agree = 0
    blog_disagree = 0
    blog_total = 0
    email_agree = 0
    email_disagree = 0
    email_total = 0

    for _row_id, blog, email, geocoding_cc in rows:
        cc, source = extract_cctld(blog, email)

        if cc is None:
            no_tld += 1
            continue

        total_by_source[source] += 1
        geocoding_upper = geocoding_cc.upper()

        if cc == geocoding_upper:
            agree += 1
            agree_by_source[source] += 1
        else:
            disagree += 1
            disagree_by_source[source] += 1
            disagree_pairs[(cc, geocoding_upper)] += 1

        # Separate Blog/Email Statistik
        blog_domain = extract_domain_from_blog(blog) if blog else None
        blog_cc_only = domain_to_country_code(blog_domain) if blog_domain else None
        if blog_cc_only:
            blog_total += 1
            if blog_cc_only == geocoding_upper:
                blog_agree += 1
            else:
                blog_disagree += 1

        email_domain = extract_domain_from_email(email) if email else None
        email_cc_only = domain_to_country_code(email_domain) if email_domain else None
        if email_cc_only:
            email_total += 1
            if email_cc_only == geocoding_upper:
                email_agree += 1
            else:
                email_disagree += 1

    evaluated = agree + disagree
    agree_rate = agree / evaluated * 100 if evaluated > 0 else 0

    print("\nErgebnisse:")
    print(f"  Evaluiert (mit TLD): {evaluated:,}")
    print(f"  Ohne verwertbare TLD: {no_tld:,}")
    print(f"  Agree: {agree:,} ({agree_rate:.1f}%)")
    print(f"  Disagree: {disagree:,} ({100 - agree_rate:.1f}%)")

    if blog_total > 0:
        print(
            f"\n  Blog-TLD separat: {blog_agree:,}/{blog_total:,} = {blog_agree/blog_total*100:.1f}%"
        )
    if email_total > 0:
        print(
            f"  Email-TLD separat: {email_agree:,}/{email_total:,} = {email_agree/email_total*100:.1f}%"
        )

    print("\n  Nach Quelle:")
    for source in sorted(total_by_source.keys()):
        src_total = total_by_source[source]
        src_agree = agree_by_source.get(source, 0)
        src_rate = src_agree / src_total * 100 if src_total > 0 else 0
        print(f"    {source}: {src_agree:,}/{src_total:,} = {src_rate:.1f}%")

    if disagree_pairs:
        print("\n  Top-15 Disagree-Paare (extrahiert -> geocoding):")
        for (ext_cc, geo_cc), cnt in disagree_pairs.most_common(15):
            print(f"    {ext_cc} -> {geo_cc}: {cnt:,}")

    # Schwellwert prüfen
    threshold = 80.0
    passed = agree_rate >= threshold
    print(f"\n  Schwellwert: {threshold}%")
    print(f"  Ergebnis: {'BESTANDEN' if passed else 'NICHT BESTANDEN'} ({agree_rate:.1f}%)")

    return {
        "total": total,
        "evaluated": evaluated,
        "agree": agree,
        "disagree": disagree,
        "no_tld": no_tld,
        "agree_rate": agree_rate,
        "passed": passed,
        "blog_agree": blog_agree,
        "blog_total": blog_total,
        "email_agree": email_agree,
        "email_total": email_total,
    }


# ============================================================
# ENRICHMENT
# ============================================================


def run_enrichment(  # noqa: C901
    conn, entity: EntityConfig, dry_run: bool, limit: Optional[int], run_id: str
) -> dict:
    """Führe ccTLD-Enrichment durch.

    Liest alle Zeilen der Tabelle ohne CC, extrahiert ccTLD, schreibt zurück.
    """
    print("\n" + "=" * 70)
    print(f"ENRICHMENT {'(DRY-RUN)' if dry_run else '(LIVE)'} - {entity.label}")
    print("=" * 70)

    # Batch-Read
    limit_clause = f"LIMIT {limit}" if limit else ""
    rows = conn.execute(
        text(
            f"""
        SELECT id, blog, email
        FROM {entity.table}
        WHERE (country_code IS NULL OR country_code = '')
        {entity.extra_target_filter}
        AND (country_code_cctld IS NULL OR country_code_cctld = '')
        AND ((blog IS NOT NULL AND blog != '')
             OR (email IS NOT NULL AND email != ''
                 AND email NOT LIKE '%@users.noreply.github.com'))
        ORDER BY id
        {limit_clause}
    """
        )
    ).fetchall()

    total = len(rows)
    print(f"Ziel-Population geladen: {total:,} {entity.label}")

    # Verarbeitung in-memory
    results = []
    cc_counter = Counter()
    source_counter = Counter()
    tld_counter = Counter()
    skipped = 0

    for row_id, blog, email in rows:
        cc, source = extract_cctld(blog, email)

        if cc is None:
            skipped += 1
            continue

        results.append(
            {
                "row_id": row_id,
                "cc": cc,
                "source": source,
            }
        )
        cc_counter[cc] += 1
        source_counter[source] += 1
        tld_counter[cc] += 1

    enriched = len(results)
    print(f"Ergebnis: {enriched:,} neue Country Codes ({skipped:,} ohne verwertbare TLD)")

    # Statistik
    print("\nNach Quelle:")
    for source, cnt in source_counter.most_common():
        print(f"  {source}: {cnt:,}")

    print("\nTop-20 Country Codes:")
    for cc, cnt in cc_counter.most_common(20):
        print(f"  {cc}: {cnt:,}")

    unique_countries = len(cc_counter)
    print(f"\nVerschiedene Laender: {unique_countries}")

    # Plausibilitäts-Check
    if enriched > 0:
        max_cc, max_cnt = cc_counter.most_common(1)[0]
        max_pct = max_cnt / enriched * 100
        print("\nPlausibilitaets-Check:")
        print(f"  Haeufigstes Land: {max_cc} ({max_pct:.1f}%)")
        print(f"  Verschiedene Laender: {unique_countries}")

        if max_pct > 30:
            print(f"  WARNUNG: {max_cc} hat >30% Anteil — manuell pruefen!")
        if unique_countries < 20:
            print("  WARNUNG: Weniger als 20 verschiedene Laender — manuell pruefen!")

    # Batch-Write
    if not dry_run and enriched > 0:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        batch_size = 1000
        updated = 0

        update_sql = text(
            f"""
            UPDATE {entity.table}
            SET country_code_cctld = :cc,
                cctld_source = :source,
                cctld_enriched_at = :now,
                cctld_run_id = :run_id
            WHERE id = :row_id
        """
        )

        batch = []
        for r in results:
            batch.append(
                {
                    "cc": r["cc"],
                    "source": r["source"],
                    "now": now,
                    "run_id": run_id,
                    "row_id": r["row_id"],
                }
            )

            if len(batch) >= batch_size:
                conn.execute(update_sql, batch)
                conn.commit()
                updated += len(batch)
                print(f"  Batch committed: {updated:,}/{enriched:,}")
                batch = []

        if batch:
            conn.execute(update_sql, batch)
            conn.commit()
            updated += len(batch)

        print(f"\nGeschrieben: {updated:,} {entity.label}")
        print(f"Run-ID: {run_id}")
        print("\nRevert SQL:")
        print(
            f"UPDATE {entity.table} SET country_code_cctld=NULL, cctld_source=NULL, "
            f"cctld_enriched_at=NULL, cctld_run_id=NULL "
            f"WHERE cctld_run_id='{run_id}';"
        )
    elif dry_run:
        print("\n[DRY-RUN] Keine Aenderungen geschrieben.")
        print("Verwende --write um zu importieren.")

    return {
        "total": total,
        "enriched": enriched,
        "skipped": skipped,
        "cc_counter": cc_counter,
        "source_counter": source_counter,
        "unique_countries": unique_countries,
    }


# ============================================================
# MAIN
# ============================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ccTLD-Enrichment: Country Code aus Blog/Email-TLD ableiten"
    )
    parser.add_argument(
        "--entity",
        choices=sorted(ENTITIES),
        default="organizations",
        help="Tabelle, die angereichert wird (default: organizations)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Keine Schreibvorgaenge (default)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Schreibe Ergebnisse nach MySQL",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Nur Validierung gegen Geocoding, kein Enrichment",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximale Anzahl zu verarbeitender Zeilen",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Custom Run-ID (default: auto-UUID)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    entity = ENTITIES[args.entity]
    dry_run = not args.write
    run_id = args.run_id or str(uuid.uuid4())

    print("=" * 70)
    print(f"CCTLD-ENRICHMENT - {entity.label.upper()}")
    print("=" * 70)
    print(f"Modus: {'DRY-RUN' if dry_run else 'LIVE-IMPORT'}")
    print(f"Run-ID: {run_id}")
    if args.limit:
        print(f"Limit: {args.limit}")
    print(f"Validierung: {'nur Validierung' if args.validate_only else 'mit Enrichment'}")
    print(f"Gefilterte TLDs: {len(FILTERED_TLDS)}")
    print(f"Compound-TLDs: {len(COMPOUND_TLD_MAP)}")
    print(f"Valide ISO-Codes: {len(VALID_ISO_CODES)}")

    engine = create_engine(DB_URL)
    conn = engine.connect()

    # Schritt 1: Validierung
    val_result = run_validation(conn, entity)

    if not val_result["passed"]:
        print(f"\nABBRUCH: Agree-Rate {val_result['agree_rate']:.1f}% < 80% Schwellwert")
        print("Kein Import moeglich. Bitte TLD-Klassifikation ueberpruefen.")
        conn.close()
        return 1

    if args.validate_only:
        print("\n[VALIDATE-ONLY] Enrichment uebersprungen.")
        conn.close()
        return 0

    # Schritt 2: Enrichment
    enr_result = run_enrichment(conn, entity, dry_run, args.limit, run_id)

    # Zusammenfassung
    print("\n" + "=" * 70)
    print("ZUSAMMENFASSUNG")
    print("=" * 70)
    print(
        f"Validierung: {val_result['agree_rate']:.1f}% Agree-Rate "
        f"({val_result['agree']:,}/{val_result['evaluated']:,})"
    )
    print(
        f"  Blog: {val_result['blog_agree']:,}/{val_result['blog_total']:,} = "
        f"{val_result['blog_agree']/val_result['blog_total']*100:.1f}%"
        if val_result["blog_total"] > 0
        else "  Blog: keine Daten"
    )
    print(
        f"  Email: {val_result['email_agree']:,}/{val_result['email_total']:,} = "
        f"{val_result['email_agree']/val_result['email_total']*100:.1f}%"
        if val_result["email_total"] > 0
        else "  Email: keine Daten"
    )
    print(f"Enrichment: {enr_result['enriched']:,} neue Country Codes")
    print(f"  Verschiedene Laender: {enr_result['unique_countries']}")
    print(f"  Modus: {'DRY-RUN' if dry_run else 'LIVE'}")

    conn.close()
    print("\nFertig.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
