#!/usr/bin/env python3
"""Wertet das Prüfprotokoll der Repository-Sammlung aus.

Die Sammlung schreibt für jede abgeschlossene Zeitperiode eine Zeile nach
`logs/collection_audit.jsonl`: die von GitHub gemeldete Trefferzahl, die tatsächlich
gesammelte Anzahl und Auffälligkeiten. Dieses Skript fasst die Datei zusammen und listet
die Perioden auf, die geprüft werden sollten:

- Fehlbetrag: es kamen weniger Repositories an, als GitHub angekündigt hatte
- unvollständig: GitHub hat mindestens eine Antwort als `incomplete_results` markiert
- abgeschnitten: mehr als 1.000 Treffer in einer nicht weiter teilbaren Periode

Liest nur die lokale Datei. Keine Datenbank, kein Netz. Exit-Code 1, wenn mindestens
eine Periode auffällig ist.

Usage:
    python scripts/diagnostics/summarize_collection_audit.py
    python scripts/diagnostics/summarize_collection_audit.py --audit-file pfad/zur/datei.jsonl
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from github_collector import config  # noqa: E402

DEFAULT_AUDIT_FILE = config.LOG_DIR / "collection_audit.jsonl"


def read_audit(path: Path) -> List[Dict[str, Any]]:
    """Liest die Zeilen des Prüfprotokolls; leere Zeilen werden übersprungen."""
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def is_flagged(entry: Dict[str, Any]) -> bool:
    """Eine Periode ist auffällig bei Fehlbetrag, unvollständiger Antwort oder Abschneiden."""
    return bool(entry["shortfall"] > 0 or entry["incomplete_pages"] > 0 or entry["truncated"])


def summarize(entries: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Fasst die Einträge zusammen und sammelt die auffälligen Perioden."""
    entries = list(entries)
    return {
        "periods": len(entries),
        "collected": sum(e["collected"] for e in entries),
        "announced": sum(min(e["total_count"], 1000) for e in entries),
        "with_shortfall": sum(1 for e in entries if e["shortfall"] > 0),
        "shortfall_total": sum(e["shortfall"] for e in entries),
        "incomplete": sum(1 for e in entries if e["incomplete_pages"] > 0),
        "truncated": sum(1 for e in entries if e["truncated"]),
        "flagged": [e for e in entries if is_flagged(e)],
    }


def parse_arguments(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Wertet das Prüfprotokoll der Sammlung aus")
    parser.add_argument(
        "--audit-file",
        type=Path,
        default=DEFAULT_AUDIT_FILE,
        help=f"Pfad zum Prüfprotokoll (default: {DEFAULT_AUDIT_FILE})",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_arguments(argv)
    if not args.audit_file.is_file():
        print(f"Kein Prüfprotokoll gefunden: {args.audit_file}", file=sys.stderr)
        return 2

    summary = summarize(read_audit(args.audit_file))
    print(f"Prüfprotokoll: {args.audit_file}")
    print(f"Abgeschlossene Perioden:        {summary['periods']:,}")
    print(f"Gesammelte Repositories:        {summary['collected']:,}")
    print(f"Von GitHub angekündigt:         {summary['announced']:,}")
    print(f"Perioden mit Fehlbetrag:        {summary['with_shortfall']:,}")
    print(f"  fehlende Repositories gesamt: {summary['shortfall_total']:,}")
    print(f"Perioden mit unvollst. Antwort: {summary['incomplete']:,}")
    print(f"Abgeschnittene Perioden:        {summary['truncated']:,}")

    if not summary["flagged"]:
        print("\nKeine auffälligen Perioden.")
        return 0

    print("\nAuffällige Perioden:")
    for e in summary["flagged"]:
        stars = (
            f"{e['min_stars']}..{e['max_stars']}"
            if e["max_stars"] is not None
            else f">={e['min_stars']}"
        )
        print(
            f"  {e['start']} bis {e['end']} (Stars {stars}): gemeldet {e['total_count']}, "
            f"gesammelt {e['collected']}, Fehlbetrag {e['shortfall']}, "
            f"unvollst. Seiten {e['incomplete_pages']}, abgeschnitten {e['truncated']}"
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
