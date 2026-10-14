#!/usr/bin/env python3
"""Bewertet Standorte neu, die auf einen mehrdeutigen US-Bundesstaat enden.

Bis 2026-10-13 ordneten die Textregeln des Geocodings jede Ortsangabe, die auf den Namen
oder das Kürzel eines US-Bundesstaats endet, den USA zu - auch "Tbilisi, Georgia",
"Perth, WA" (Western Australia) und "London, CA". Seit der Korrektur entscheidet für diese
Endungen der Geocoder, sofern der Text davor kein bekannter US-Ort ist.

Dieses Skript wendet die korrigierte Regel auf die vorhandenen Zeilen an:

1. Es wählt Contributors und Organisationen, deren Standort nach einem Komma oder
   Leerzeichen auf "georgia", "wa" oder "ca" endet. Die bloße Angabe "Georgia" ist nicht
   entscheidbar und wird nicht angefasst.
2. Für jede verschiedene Ortsangabe bestimmt es das Land neu. Entscheiden die Textregeln
   weiterhin (bekannter US-Ort davor), wird der Geocoder nicht gefragt. Sonst wird der
   Cache-Eintrag verworfen und der Geocoder gefragt.
3. Weicht das neue Land vom gespeicherten ab, wird die Zeile geändert. Liefert der
   Geocoder kein Land oder antwortet er nicht, bleibt die Zeile unverändert.

Ohne `--write` wird nichts geschrieben (Probelauf): weder in die Datenbank noch in den
Cache der Arbeitskopie - der Probelauf arbeitet mit einer Kopie des Caches.

Usage:
    python scripts/maintenance/recheck_ambiguous_locations.py              # Probelauf
    python scripts/maintenance/recheck_ambiguous_locations.py --limit 200  # nur 200 Ortsangaben
    python scripts/maintenance/recheck_ambiguous_locations.py --write      # Änderungen schreiben
"""

import argparse
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from sqlalchemy import text  # noqa: E402

from github_collector import config  # noqa: E402
from github_collector.database.engine import make_engine  # noqa: E402
from github_collector.geocoding.geocoding_service import GeocodingService  # noqa: E402
from github_collector.geocoding.normalization import normalize_location  # noqa: E402

TABLES = ("contributors", "organizations")

# Standorte, die auf eine der mehrdeutigen Endungen enden (nach Komma oder Leerzeichen)
SELECT_ROWS = """
    SELECT id, location, country_code
    FROM {table}
    WHERE location IS NOT NULL
      AND (LOWER(location) LIKE '%, georgia' OR LOWER(location) LIKE '% georgia'
           OR LOWER(location) LIKE '%, wa' OR LOWER(location) LIKE '% wa'
           OR LOWER(location) LIKE '%, ca' OR LOWER(location) LIKE '% ca')
"""
UPDATE_ROW = "UPDATE {table} SET country_code = :country_code, region = :region WHERE id = :id"


def select_rows(engine, table: str) -> List[Tuple[int, str, Optional[str]]]:
    with engine.connect() as conn:
        return [tuple(row) for row in conn.execute(text(SELECT_ROWS.format(table=table)))]


def resolve(location: str, service: GeocodingService) -> Tuple[Optional[str], Optional[str], str]:
    """Bestimme das Land nach der korrigierten Regel.

    Returns:
        (country_code, region, how) mit how in {"rule", "geocoder", "no_result", "unanswered"}.
    """
    normalized = normalize_location(location) or ""
    code, region = service.heuristics.extract_country_from_text(normalized)
    if code:
        return code, region, "rule"

    if service.cache is not None:
        service.cache.remove(location)
    result = service.geocode(location)
    if service.last_request_unanswered:
        return None, None, "unanswered"
    country_code = (result or {}).get("country_code") or ""
    if len(country_code) != 2:
        return None, None, "no_result"
    return country_code.upper(), result.get("region"), "geocoder"


def group_by_location(rows) -> Dict[str, List[Tuple[int, Optional[str]]]]:
    """Fasse die Zeilen je Ortsangabe zusammen: jede Angabe wird nur einmal neu bewertet."""
    grouped: Dict[str, List[Tuple[int, Optional[str]]]] = defaultdict(list)
    for row_id, location, country_code in rows:
        grouped[location].append((row_id, country_code))
    return grouped


def apply_result(engine, table: str, rows, new_code: str, region, write: bool, summary) -> None:
    """Zähle die Zeilen, deren Land sich ändert, und schreibe sie bei write=True."""
    changes = []
    for row_id, old in rows:
        if (old or "").upper() != new_code:
            changes.append(row_id)
            summary["transitions"][f"{(old or '-').upper()} -> {new_code}"] += 1
    summary["changed_rows"] += len(changes)
    if write and changes:
        with engine.begin() as conn:
            conn.execute(
                text(UPDATE_ROW.format(table=table)),
                [{"id": i, "country_code": new_code, "region": region} for i in changes],
            )


def recheck(engine, service: GeocodingService, write: bool, limit: Optional[int]) -> Dict[str, Any]:
    """Bewerte die betroffenen Zeilen neu; schreibe Änderungen nur bei write=True."""
    summary: Dict[str, Any] = {
        "rows": 0,
        "locations": 0,
        "how": Counter(),
        "transitions": Counter(),
        "changed_rows": 0,
    }
    resolved: Dict[str, Tuple[Optional[str], Optional[str], str]] = {}

    for table in TABLES:
        grouped = group_by_location(select_rows(engine, table))
        summary["rows"] += sum(len(rows) for rows in grouped.values())

        for location, rows in grouped.items():
            if location not in resolved:
                if limit is not None and len(resolved) >= limit:
                    continue
                resolved[location] = resolve(location, service)
                summary["how"][resolved[location][2]] += 1
            new_code, region, _how = resolved[location]
            # Kein Ergebnis oder keine Antwort: die Zeile bleibt, wie sie ist
            if new_code:
                apply_result(engine, table, rows, new_code, region, write, summary)

    summary["locations"] = len(resolved)
    if service.cache is not None:
        service.cache.flush()
    return summary


def parse_arguments(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bewertet Standorte mit mehrdeutiger US-Endung (Georgia, WA, CA) neu"
    )
    parser.add_argument(
        "--write", action="store_true", help="Änderungen schreiben (default: Probelauf)"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Höchstzahl verschiedener Ortsangaben"
    )
    parser.add_argument(
        "--cache-file",
        default=str(config.CACHE_CONFIG["geocoding_file"]),
        help="Pfad zum Geocoding-Cache",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None, service: Optional[GeocodingService] = None) -> int:
    args = parse_arguments(argv)
    engine = make_engine(str(config.DB_URL))

    if service is None:
        cache_file = args.cache_file
        if not args.write and Path(cache_file).is_file():
            # Probelauf: den echten Cache nicht verändern
            scratch = Path(tempfile.mkdtemp(prefix="recheck_cache_")) / "geocoding_cache.json"
            shutil.copyfile(cache_file, scratch)
            cache_file = str(scratch)
        service = GeocodingService(cache_file=cache_file)

    summary = recheck(engine, service, args.write, args.limit)

    print("Modus:", "SCHREIBEN" if args.write else "PROBELAUF (nichts geschrieben)")
    print(f"Betroffene Zeilen (Standort endet mehrdeutig): {summary['rows']:,}")
    print(f"Verschiedene Ortsangaben neu bewertet:         {summary['locations']:,}")
    for how, count in summary["how"].most_common():
        print(f"  {how:<11} {count:,}")
    print(f"Zeilen mit geändertem Land:                    {summary['changed_rows']:,}")
    for transition, count in summary["transitions"].most_common(25):
        print(f"  {transition}: {count:,}")

    unanswered = summary["how"].get("unanswered", 0)
    if unanswered:
        print(
            f"\n{unanswered} Ortsangaben blieben unbeantwortet; ein erneuter Aufruf fragt sie "
            "noch einmal an.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
