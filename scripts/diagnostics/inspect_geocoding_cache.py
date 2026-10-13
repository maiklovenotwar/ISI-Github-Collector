#!/usr/bin/env python3
"""Wertet den lokalen Geocoding-Cache aus, ohne den Geocoding-Dienst abzufragen.

Zählt die Einträge mit und ohne Land und teilt die Einträge ohne Land danach auf, ob die
heutigen Regeln des Programms (Sonderfall-Tabelle, Texterkennung, Nicht-Orte) sie erklären:

- offline auflösbar: die Regeln liefern heute ein Land - der leere Eintrag ist überholt
- kein Ort:          die Regeln erkennen die Angabe als nicht-geographisch
- sonstige:          weder noch - hier lässt sich nicht unterscheiden, ob der Dienst
                     "kein Treffer" geantwortet oder (vor der Korrektur vom 2026-10-12)
                     gar nicht geantwortet hat

Liest nur die lokale Cache-Datei. Keine Datenbank, kein Netz, schreibt nichts.

Usage:
    python scripts/diagnostics/inspect_geocoding_cache.py
    python scripts/diagnostics/inspect_geocoding_cache.py --examples 20
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from github_collector import config  # noqa: E402
from github_collector.cli.geocoding_command import (  # noqa: E402
    NON_GEO_LOCATIONS,
    is_non_geographic_location,
)
from github_collector.geocoding.cache import GeocodingCache  # noqa: E402
from github_collector.geocoding.heuristics import HeuristicResolver  # noqa: E402

DEFAULT_CACHE_FILE = Path(config.CACHE_CONFIG["geocoding_file"])


def classify_empty(key: str, resolver: HeuristicResolver) -> str:
    """Ordne einen Cache-Schlüssel ohne Land einer der drei Gruppen zu."""
    mapped = resolver.resolve_mapping(key)
    if mapped:
        return "resolvable_offline"
    if mapped is not None or is_non_geographic_location(key, NON_GEO_LOCATIONS):
        return "non_geographic"
    code, _region = resolver.extract_country_from_text(key)
    return "resolvable_offline" if code else "other"


def summarize(cache: Dict[str, Dict[str, Any]], resolver: HeuristicResolver) -> Dict[str, Any]:
    """Zähle die Einträge des Caches nach Ergebnis und, soweit vorhanden, nach Herkunft."""
    summary: Dict[str, Any] = {
        "total": len(cache),
        "with_country": 0,
        "empty": 0,
        "empty_resolvable_offline": 0,
        "empty_non_geographic": 0,
        "empty_other": 0,
        "without_metadata": 0,
        "sources": Counter(),
        "other_keys": [],
    }
    for key, entry in cache.items():
        if not entry or "source" not in entry:
            summary["without_metadata"] += 1
        else:
            summary["sources"][entry["source"]] += 1

        if not GeocodingCache.is_empty_result(entry):
            summary["with_country"] += 1
            continue

        summary["empty"] += 1
        group = classify_empty(key, resolver)
        summary[f"empty_{group}"] += 1
        if group == "other":
            summary["other_keys"].append(key)
    return summary


def parse_arguments(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Wertet den lokalen Geocoding-Cache aus")
    parser.add_argument(
        "--cache-file",
        type=Path,
        default=DEFAULT_CACHE_FILE,
        help=f"Pfad zur Cache-Datei (default: {DEFAULT_CACHE_FILE})",
    )
    parser.add_argument(
        "--examples",
        type=int,
        default=0,
        help="Anzahl Beispiele aus der Gruppe 'sonstige' ausgeben (default: 0)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_arguments(argv)
    if not args.cache_file.is_file():
        print(f"Keine Cache-Datei gefunden: {args.cache_file}", file=sys.stderr)
        return 2

    with open(args.cache_file, encoding="utf-8") as handle:
        cache = json.load(handle)

    s = summarize(cache, HeuristicResolver())
    total = s["total"] or 1
    print(f"Cache-Datei: {args.cache_file}")
    print(f"Einträge gesamt:          {s['total']:,}")
    print(f"  mit Land:               {s['with_country']:,} ({s['with_country'] / total:.1%})")
    print(f"  ohne Land:              {s['empty']:,} ({s['empty'] / total:.1%})")
    print(f"    offline auflösbar:    {s['empty_resolvable_offline']:,}")
    print(f"    kein Ort:             {s['empty_non_geographic']:,}")
    print(f"    sonstige:             {s['empty_other']:,}")
    print(f"Einträge ohne Herkunftsangabe (vor 2026-10-12 geschrieben): {s['without_metadata']:,}")
    for source, count in s["sources"].most_common():
        print(f"  Herkunft {source}: {count:,}")
    if args.examples > 0:
        step = max(1, len(s["other_keys"]) // args.examples)
        print(f"\nBeispiele 'sonstige' (jeder {step}. Schlüssel):")
        for key in s["other_keys"][::step][: args.examples]:
            print(f"  {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
