#!/usr/bin/env python3
"""Setzt Länder zurück, die nicht mehr zum gespeicherten Ort passen (Befund K3).

Die Profile aller Konten wurden 2026-08/09 erneut abgerufen; dabei wurde `location`
überschrieben, `country_code` blieb. Dieses Skript bestimmt für jede Zeile mit Land und Ort
das Land, das die Geokodierung heute für den Ort liefern würde, ohne einen Dienst zu fragen:

1. eine kuratierte Zuordnung, die ein Land nennt (geht dem Cache vor, Befund K10),
2. sonst der Geocoding-Cache,
3. sonst die Textregeln.

Zwei Gruppen werden zurückgesetzt (`country_code` und `region` auf NULL), damit
`update_location_geocoding.py` sie neu bestimmt:

- "different": das heutige Ergebnis nennt ein anderes Land,
- "not_in_cache": der Ort wurde nie geokodiert und keine Regel entscheidet ihn.

Unberührt bleiben Zeilen, deren Ort laut Cache oder Zuordnung ausdrücklich kein Land hat.

Ohne `--write` wird nur gezählt (Probelauf). Mit `--write` werden die betroffenen Zeilen vor
dem Schreiben mit id, location, country_code und region als CSV gesichert (Standard:
data/backups/); ein Zurücksetzen lässt sich nur mit dieser Sicherung rückgängig machen.

Usage:
    python scripts/maintenance/reset_stale_countries.py              # Probelauf
    python scripts/maintenance/reset_stale_countries.py --write      # sichern und zurücksetzen
"""

import argparse
import csv
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from sqlalchemy import bindparam, text  # noqa: E402

from github_collector import config  # noqa: E402
from github_collector.database.engine import make_engine  # noqa: E402
from github_collector.geocoding.cache import GeocodingCache  # noqa: E402
from github_collector.geocoding.geocoding_service import preprocess_location  # noqa: E402
from github_collector.geocoding.heuristics import HeuristicResolver  # noqa: E402
from github_collector.geocoding.normalization import normalize_location  # noqa: E402

TABLES = ("contributors", "organizations")
DEFAULT_BACKUP_DIR = Path(__file__).parent.parent.parent / "data" / "backups"
BATCH_SIZE = 1000

SELECT_ROWS = """
    SELECT id, location, country_code
    FROM {table}
    WHERE country_code IS NOT NULL AND country_code != ''
      AND location IS NOT NULL AND location != ''
"""

# Marker für "der Ort hat heute ausdrücklich kein Land" bzw. "nie geokodiert"
NO_COUNTRY = ""
UNKNOWN = None


def current_country(location: str, cache: GeocodingCache, rules: HeuristicResolver):
    """Land, das die Geokodierung heute für den Ort liefern würde, ohne Dienst-Anfrage.

    Bildet die Reihenfolge von GeocodingService.geocode() nach, einschließlich der
    Bereinigung (z. B. "Remote, Korea, South" -> "korea, south").

    Returns:
        Ländercode, NO_COUNTRY (ausdrücklich kein Land) oder UNKNOWN (nie geokodiert).
    """
    processed = preprocess_location(location)
    normalized = normalize_location(processed) if processed else ""
    mapped = rules.resolve_mapping(normalized) if normalized else None
    if mapped:
        return (mapped.get("country_code") or "").upper()
    cached = cache.get(location)
    if cached is not None:
        return (cached.get("country_code") or "").upper()
    if not normalized or mapped is not None:
        return NO_COUNTRY
    code, _region = rules.extract_country_from_text(normalized)
    return code.upper() if code else UNKNOWN


def find_stale(
    conn, table: str, cache: GeocodingCache, rules: HeuristicResolver
) -> Dict[str, List[int]]:
    """IDs der Zeilen, deren gespeichertes Land nicht mehr zum Ort passt, nach Gruppe."""
    stale: Dict[str, List[int]] = {"different": [], "not_in_cache": []}
    known: Dict[str, Optional[str]] = {}
    result = conn.execution_options(stream_results=True).execute(
        text(SELECT_ROWS.format(table=table))
    )
    for row_id, location, stored in result:
        if location not in known:
            known[location] = current_country(location, cache, rules)
        now = known[location]
        if now is UNKNOWN:
            stale["not_in_cache"].append(row_id)
        elif now != NO_COUNTRY and now != stored.upper():
            stale["different"].append(row_id)
    return stale


def backup_rows(conn, table: str, ids: List[int], backup_dir: Path) -> Path:
    """Sichert id, location, country_code und region der Zeilen als CSV."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = backup_dir / f"reset_stale_countries_{table}_{stamp}.csv"
    stmt = text(
        f"SELECT id, location, country_code, region FROM {table} WHERE id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    written = 0
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "location", "country_code", "region"])
        for i in range(0, len(ids), BATCH_SIZE):
            for row in conn.execute(stmt, {"ids": ids[i : i + BATCH_SIZE]}):
                writer.writerow(list(row))
                written += 1
    if written != len(ids):
        raise RuntimeError(f"Sicherung unvollständig: {written} von {len(ids)} Zeilen")
    return path


def reset_rows(engine, table: str, ids: List[int]) -> None:
    stmt = text(
        f"UPDATE {table} SET country_code = NULL, region = NULL WHERE id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    for i in range(0, len(ids), BATCH_SIZE):
        with engine.begin() as conn:
            conn.execute(stmt, {"ids": ids[i : i + BATCH_SIZE]})


def parse_arguments(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Länder zurücksetzen, die nicht mehr zum gespeicherten Ort passen"
    )
    parser.add_argument("--write", action="store_true", help="sichern und zurücksetzen")
    parser.add_argument("--table", choices=TABLES, help="nur diese Tabelle (Standard: beide)")
    parser.add_argument("--cache-file", help="Geocoding-Cache (Standard: aus der Konfiguration)")
    parser.add_argument(
        "--backup-dir", default=str(DEFAULT_BACKUP_DIR), help="Ordner für die CSV-Sicherung"
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_arguments(argv)
    cache = GeocodingCache(args.cache_file) if args.cache_file else GeocodingCache()
    rules = HeuristicResolver()
    engine = make_engine(config.DB_URL)
    tables = (args.table,) if args.table else TABLES
    print(f"Modus: {'SCHREIBEN' if args.write else 'PROBELAUF'}")
    print(f"Geocoding-Cache: {len(cache.cache):,} Einträge")
    # Ein leerer Cache (falscher Pfad, unlesbare Datei) machte fast jede Zeile zu "nie
    # geokodiert" und setzte Millionen Länder zurück.
    if args.write and not cache.cache:
        print("ABBRUCH: Der Geocoding-Cache ist leer; ohne ihn wird nichts zurückgesetzt.")
        return 1

    for table in tables:
        with engine.connect() as conn:
            stale = find_stale(conn, table, cache, rules)
        ids = stale["different"] + stale["not_in_cache"]
        counts = Counter({group: len(group_ids) for group, group_ids in stale.items()})
        print(
            f"{table}: anderes Land {counts['different']:,}, "
            f"nie geokodiert {counts['not_in_cache']:,}, zusammen {len(ids):,}"
        )
        if not args.write or not ids:
            continue
        with engine.connect() as conn:
            path = backup_rows(conn, table, ids, Path(args.backup_dir))
        print(f"  gesichert: {len(ids):,} Zeilen in {path}")
        reset_rows(engine, table, ids)
        print(f"  zurückgesetzt: {len(ids):,} Zeilen")

    if not args.write:
        print("Probelauf: nichts geschrieben. Mit --write sichern und zurücksetzen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
