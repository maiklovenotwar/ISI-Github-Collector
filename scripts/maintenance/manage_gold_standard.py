#!/usr/bin/env python3
"""
Gold-Standard-Datensatz für Geocoding-Qualitätssicherung verwalten.

Dieses Tool ermöglicht:
- sample: Ziehe repräsentative Locations aus der Datenbank
- label: Interaktives Labeling mit System-Vorschlägen
- stats: Zeige Statistiken zum Gold-Standard-Datensatz

Der Gold-Standard wird in tests/resources/geocoding_gold_standard.json gespeichert.
"""

import argparse
import json
import logging
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Set

from sqlalchemy import func, text

from github_collector.database.database import session_scope
from github_collector.database.models import Contributor
from github_collector.geocoding.geocoding_service import GeocodingService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Pfad zum Gold-Standard-Datensatz
GOLD_STANDARD_PATH = (
    Path(__file__).parent.parent.parent / "tests" / "resources" / "geocoding_gold_standard.json"
)

# Problem-Länder für gezieltes Sampling
PROBLEM_COUNTRIES = {
    "SH",
    "SZ",
    "BJ",
    "FM",
    "BE",
    "CH",
    "AT",
    "NL",
    "ID",
    "VA",
    "LA",
    "SD",
    "GA",
    "SC",
    "MA",
    "SA",
    "OM",
}

# Länder für stratified sampling (wichtige Tech-Nationen)
STRATIFIED_COUNTRIES: List[str] = [
    "CN",
    "US",
    "IN",
    "DE",
    "BR",
    "GB",
    "FR",
    "RU",
    "ID",
    "JP",
    "NG",
]


def load_gold_standard() -> List[Dict[str, Any]]:
    """Lade den Gold-Standard-Datensatz."""
    if not GOLD_STANDARD_PATH.exists():
        return []
    with open(GOLD_STANDARD_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_gold_standard(data: List[Dict[str, Any]]) -> None:
    """Speichere den Gold-Standard-Datensatz."""
    GOLD_STANDARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(GOLD_STANDARD_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"Gold-Standard gespeichert: {GOLD_STANDARD_PATH}")


def cmd_sample(args: argparse.Namespace) -> int:  # noqa: C901
    """Ziehe repräsentative Locations aus der Datenbank."""
    n = args.count
    logger.info(f"Ziehe {n} Locations aus der Datenbank...")

    # Berechne Anteile
    n_frequent = int(n * 0.3)  # 30% häufigste
    n_stratified = int(n * 0.3)  # 30% stratified nach Ländern
    n_problem = int(n * 0.2)  # 20% Problem-Länder
    n_random = n - n_frequent - n_stratified - n_problem  # Rest zufällig

    samples: List[str] = []

    with session_scope() as session:
        # 1. Häufigste Locations (30%)
        logger.info(f"  Ziehe {n_frequent} häufigste Locations...")
        frequent_query = (
            session.query(Contributor.location, func.count(Contributor.id).label("cnt"))
            .filter(Contributor.location.isnot(None))
            .filter(Contributor.location != "")
            .group_by(Contributor.location)
            .order_by(text("cnt DESC"))
            .limit(n_frequent * 2)  # Mehr ziehen, um Duplikate zu vermeiden
        )
        frequent_locations = [row[0] for row in frequent_query.all()]
        samples.extend(frequent_locations[:n_frequent])

        # 2. Stratified Sampling nach Ländern (30%)
        if STRATIFIED_COUNTRIES and n_stratified > 0:
            count_per_country = max(1, int(n_stratified / len(STRATIFIED_COUNTRIES)))
            logger.info(
                "  Stratified-Sampling: %s Länder, ca. %s Locations pro Land (Ziel: %s)",
                len(STRATIFIED_COUNTRIES),
                count_per_country,
                n_stratified,
            )

            existing: Set[str] = set(samples)
            stratified_added = 0

            for country_code in STRATIFIED_COUNTRIES:
                if stratified_added >= n_stratified:
                    break

                query = (
                    session.query(Contributor.location)
                    .filter(Contributor.country_code == country_code)
                    .filter(Contributor.location.isnot(None))
                    .filter(Contributor.location != "")
                    .distinct()
                    .limit(100)
                )
                candidates = [row[0] for row in query.all()]
                if not candidates:
                    continue
                random.shuffle(candidates)

                added_for_country = 0
                for loc in candidates:
                    if loc in existing:
                        continue
                    samples.append(loc)
                    existing.add(loc)
                    stratified_added += 1
                    added_for_country += 1
                    if added_for_country >= count_per_country or stratified_added >= n_stratified:
                        break

            logger.info("  Stratified-Samples hinzugefügt: %s", stratified_added)

        # 3. Locations aus Problem-Ländern (20%)
        logger.info(f"  Ziehe {n_problem} Locations aus Problem-Ländern...")
        problem_query = (
            session.query(Contributor.location)
            .filter(Contributor.country_code.in_(PROBLEM_COUNTRIES))
            .filter(Contributor.location.isnot(None))
            .filter(Contributor.location != "")
            .distinct()
            .limit(n_problem * 3)
        )
        problem_locations = [row[0] for row in problem_query.all()]
        random.shuffle(problem_locations)
        samples.extend(problem_locations[:n_problem])

        # 4. Zufällige Locations (20%)
        logger.info(f"  Ziehe {n_random} zufällige Locations...")
        # Hole alle distinct Locations und wähle zufällig
        random_query = (
            session.query(Contributor.location)
            .filter(Contributor.location.isnot(None))
            .filter(Contributor.location != "")
            .distinct()
            .limit(10000)  # Limit für Performance
        )
        all_locations = [row[0] for row in random_query.all()]
        random.shuffle(all_locations)

        # Füge nur Locations hinzu, die noch nicht in samples sind
        existing = set(samples)
        for loc in all_locations:
            if loc not in existing:
                samples.append(loc)
                existing.add(loc)
                if len(samples) >= n:
                    break

    # Entferne Duplikate und limitiere
    samples = list(dict.fromkeys(samples))[:n]

    logger.info(f"Gezogene Samples: {len(samples)}")

    # Speichere als temporäre Datei für das Labeling
    sample_path = GOLD_STANDARD_PATH.parent / "geocoding_samples.json"
    with open(sample_path, "w", encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=2)
    logger.info(f"Samples gespeichert: {sample_path}")

    return 0


def cmd_label(args: argparse.Namespace) -> int:  # noqa: C901
    """Interaktives Labeling der Samples."""
    sample_path = GOLD_STANDARD_PATH.parent / "geocoding_samples.json"

    if not sample_path.exists():
        logger.error("Keine Samples gefunden. Führe zuerst 'sample' aus.")
        return 1

    with open(sample_path, "r", encoding="utf-8") as f:
        samples = json.load(f)

    # Lade existierenden Gold-Standard
    gold_standard = load_gold_standard()
    labeled_locations: Set[str] = {entry["location"] for entry in gold_standard}

    # Filtere bereits gelabelte Locations
    unlabeled = [loc for loc in samples if loc not in labeled_locations]

    if not unlabeled:
        logger.info("Alle Samples sind bereits gelabelt!")
        return 0

    logger.info(f"Starte Labeling für {len(unlabeled)} Locations...")
    logger.info(
        "Befehle: [Enter]=Akzeptieren, [n]=Non-Geo (None), [Code]=Manueller ISO-Code, [q]=Beenden"
    )
    logger.info("-" * 60)

    # Initialisiere GeocodingService für Vorschläge
    geocoding_service = GeocodingService()

    new_labels = 0
    try:
        for i, location in enumerate(unlabeled, 1):
            # Hole System-Vorschlag
            result = geocoding_service.geocode(location)
            suggested_code = result.get("country_code", "") if result else ""

            # Zeige Prompt
            print(f'\n[{i}/{len(unlabeled)}] Location: "{location}"')
            if suggested_code:
                print(f"    Vorschlag System: {suggested_code}")
            else:
                print("    Vorschlag System: (kein Ergebnis)")

            user_input = input("    Eingabe: ").strip().upper()

            if user_input in ("Q", "QUIT"):
                logger.info("Labeling abgebrochen.")
                break
            elif user_input in ("N", "NONE", "-"):
                # Non-Geo
                gold_standard.append({"location": location, "expected": None})
                new_labels += 1
            elif user_input == "":
                # Akzeptiere Vorschlag
                if suggested_code:
                    gold_standard.append({"location": location, "expected": suggested_code})
                else:
                    gold_standard.append({"location": location, "expected": None})
                new_labels += 1
            elif len(user_input) == 2 and user_input.isalpha():
                # Manueller ISO-Code
                gold_standard.append({"location": location, "expected": user_input})
                new_labels += 1
            else:
                print(f"    Ungültige Eingabe: {user_input}")
                continue

            # Speichere nach jedem Label (für Crash-Sicherheit)
            save_gold_standard(gold_standard)

    except KeyboardInterrupt:
        logger.info("\nLabeling durch Ctrl+C abgebrochen.")

    logger.info(f"\n{new_labels} neue Labels hinzugefügt.")
    logger.info(f"Gesamt: {len(gold_standard)} gelabelte Locations.")

    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    """Zeige Statistiken zum Gold-Standard-Datensatz."""
    gold_standard = load_gold_standard()

    if not gold_standard:
        logger.info("Kein Gold-Standard-Datensatz vorhanden.")
        return 0

    total = len(gold_standard)
    non_geo = sum(1 for entry in gold_standard if entry.get("expected") is None)
    geo = total - non_geo

    # Zähle pro Land
    country_counts: Dict[str, int] = {}
    for entry in gold_standard:
        code = entry.get("expected")
        if code:
            country_counts[code] = country_counts.get(code, 0) + 1

    print("\n" + "=" * 60)
    print("Gold-Standard Statistiken")
    print("=" * 60)
    print(f"Gesamt gelabelt:     {total}")
    print(f"  - Geographisch:    {geo} ({100*geo/total:.1f}%)")
    print(f"  - Non-Geo (None):  {non_geo} ({100*non_geo/total:.1f}%)")
    print()
    print("Top 10 Länder:")
    for code, count in sorted(country_counts.items(), key=lambda x: -x[1])[:10]:
        print(f"  {code}: {count}")
    print("=" * 60)

    return 0


def main() -> int:
    """Hauptfunktion."""
    parser = argparse.ArgumentParser(
        description="Gold-Standard-Datensatz für Geocoding-Qualitätssicherung verwalten"
    )
    subparsers = parser.add_subparsers(dest="command", help="Verfügbare Befehle")

    # sample Subcommand
    sample_parser = subparsers.add_parser(
        "sample", help="Ziehe repräsentative Locations aus der DB"
    )
    sample_parser.add_argument(
        "-n",
        "--count",
        type=int,
        default=100,
        help="Anzahl der zu ziehenden Locations (default: 100)",
    )

    # label Subcommand
    subparsers.add_parser("label", help="Interaktives Labeling der Samples")

    # stats Subcommand
    subparsers.add_parser("stats", help="Zeige Statistiken zum Gold-Standard")

    args = parser.parse_args()

    if args.command == "sample":
        return cmd_sample(args)
    elif args.command == "label":
        return cmd_label(args)
    elif args.command == "stats":
        return cmd_stats(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
