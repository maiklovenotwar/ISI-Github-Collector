#!/usr/bin/env python3
"""Benchmark des GeocodingService gegen den Gold-Standard-Datensatz.

Dieses Skript lädt ``tests/resources/geocoding_gold_standard.json`` und
lässt den aktuellen ``GeocodingService`` für jede Location laufen.

Es berechnet:
- Accuracy
- True Positives (TP)
- True Negatives (TN)
- False Positives (FP)
- False Negatives (FN)
- Mismatches (falsches Land)

Es führt **keine** schreibenden Datenbank-Operationen aus.
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from github_collector.geocoding.geocoding_service import GeocodingService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def load_gold_standard(path: Path) -> List[Dict[str, Any]]:
    """Lade den Gold-Standard-Datensatz von der angegebenen Datei.

    Args:
        path: Pfad zur JSON-Datei.

    Returns:
        Liste von Einträgen mit `location` und `expected`.
    """
    if not path.exists():
        logger.error("Gold-Standard-Datei nicht gefunden: %s", path)
        logger.error("Stelle sicher, dass tests/resources/geocoding_gold_standard.json existiert.")
        sys.exit(1)

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        logger.error("Gold-Standard-Datei hat unerwartetes Format (kein JSON-Array).")
        sys.exit(1)

    return data


def _extract_country_code(result: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extrahiere den Country-Code aus einem Geocoding-Ergebnis.

    Args:
        result: Ergebnis von GeocodingService.geocode.

    Returns:
        ISO-Ländercode oder None, wenn kein Land bestimmt wurde.
    """
    if not result:
        return None
    code = result.get("country_code")
    if not code:
        return None
    if not isinstance(code, str):
        return None
    code = code.strip().upper()
    return code or None


def _init_service(use_cache: bool = True) -> GeocodingService:
    """Initialisiere den GeocodingService für den Benchmark."""
    if use_cache:
        logger.info("Initialisiere GeocodingService (mit Cache).")
        logger.warning(
            "Hinweis: Cache ist aktiviert. Ergebnisse können aus dem Cache kommen, "
            "nicht nur aus frischer Logik."
        )
        return GeocodingService()
    else:
        logger.info("Initialisiere GeocodingService (OHNE Cache).")
        return GeocodingService(use_cache=False)


def _evaluate_sample(
    location: str,
    expected: Optional[str],
    service: GeocodingService,
    counters: Dict[str, int],
    error_details: List[str],
) -> None:
    """Bewerte einen einzelnen Gold-Standard-Eintrag und aktualisiere Zähler.

    Args:
        location: Roh-Location aus dem Gold-Standard.
        expected: Erwarteter ISO-Ländercode oder None.
        service: GeocodingService-Instanz.
        counters: Mutable Dict mit Zählern für Metriken.
        error_details: Liste der Fehlerdetail-Strings.
    """

    expected_code = expected.strip().upper() if expected else None
    result = service.geocode(location)
    actual_code = _extract_country_code(result)

    # Herkunft des Ergebnisses (Sonderfall-Tabelle, Texterkennung, Nominatim) mitzählen;
    # unbeantwortete Anfragen getrennt ausweisen, weil sie nichts über das Verfahren sagen.
    if getattr(service, "last_request_unanswered", False) is True:
        source = "unanswered"
    else:
        source = (result or {}).get("source", "no_result")
    counters[f"source_{source}_total"] = counters.get(f"source_{source}_total", 0) + 1
    if source == "unanswered":
        # Keine Aussage über das Verfahren: weder als richtig noch als falsch werten
        counters["unanswered"] = counters.get("unanswered", 0) + 1
        return
    if expected_code == actual_code:
        counters[f"source_{source}_correct"] = counters.get(f"source_{source}_correct", 0) + 1

    if expected_code == actual_code:
        counters["correct"] += 1
        if expected_code is None:
            counters["tn"] += 1
        else:
            counters["tp"] += 1
        return

    if expected_code is None and actual_code is not None:
        counters["fp"] += 1
        error_details.append(
            f'[FP]       Location: "{location}" | Expected: None | Got: {actual_code}'
        )
    elif expected_code is not None and actual_code is None:
        counters["fn"] += 1
        error_details.append(
            f'[FN]       Location: "{location}" | Expected: {expected_code} | Got: None'
        )
    elif expected_code is not None and actual_code is not None:
        counters["mismatches"] += 1
        error_details.append(
            f'[MISMATCH] Location: "{location}" | Expected: {expected_code} | Got: {actual_code}'
        )
    else:
        error_details.append(
            f'[UNKNOWN]  Location: "{location}" | Expected: {expected_code} | Got: {actual_code}'
        )


def _print_report(total: int, counters: Dict[str, int], error_details: List[str]) -> None:
    """Gib die Benchmark-Ergebnisse formatiert auf stdout aus."""

    correct = counters["correct"]
    unanswered = counters.get("unanswered", 0)
    evaluated = total - unanswered
    accuracy = (correct / evaluated) * 100.0 if evaluated else 0.0

    print("\n" + "=" * 60)
    print("Geocoding Benchmark gegen Gold-Standard")
    print("=" * 60)
    print(f"Samples gesamt: {total}")
    print(f"Nicht beantwortet (nicht gewertet): {unanswered}")
    print(f"Korrekt:        {correct} ({accuracy:.2f}%)")
    print("")
    print("True Positives (Geo korrekt):   ", counters["tp"])
    print("True Negatives (Non-Geo korrekt):", counters["tn"])
    print("False Positives (Non-Geo -> Geo):", counters["fp"])
    print("False Negatives (Geo -> None):   ", counters["fn"])
    print("Mismatches (falsches Land):      ", counters["mismatches"])
    print("")
    print("Nach Herkunft des Ergebnisses (korrekt / gesamt):")
    for key in sorted(k for k in counters if k.startswith("source_") and k.endswith("_total")):
        name = key[len("source_") : -len("_total")]
        print(f"  {name:<12} {counters.get(f'source_{name}_correct', 0)} / {counters[key]}")
    print("=" * 60)

    if error_details:
        print("Fehlerdetails:")
        for line in error_details:
            # Encode für Windows-Konsole (cp1252) mit Ersatzzeichen
            try:
                print(line)
            except UnicodeEncodeError:
                print(line.encode("ascii", errors="replace").decode("ascii"))
        print("=" * 60)
    else:
        print("Keine Fehler – alle Einträge korrekt geocodiert.")
        print("=" * 60)


def benchmark(use_cache: bool = True) -> int:
    """Führe den Benchmark gegen den Gold-Standard aus."""

    repo_root = Path(__file__).resolve().parents[2]
    gold_path = repo_root / "tests" / "resources" / "geocoding_gold_standard.json"

    logger.info("Lade Gold-Standard von: %s", gold_path)
    gold_data = load_gold_standard(gold_path)

    total = len(gold_data)
    if total == 0:
        logger.warning("Gold-Standard enthält keine Einträge.")
        return 0

    service = _init_service(use_cache=use_cache)

    counters: Dict[str, int] = {
        "correct": 0,
        "tp": 0,
        "tn": 0,
        "fp": 0,
        "fn": 0,
        "mismatches": 0,
    }
    error_details: List[str] = []

    for entry in gold_data:
        location = entry.get("location")
        expected = entry.get("expected")

        if location is None:
            continue

        expected_code: Optional[str] = None
        if expected is not None:
            expected_code = str(expected)

        _evaluate_sample(location, expected_code, service, counters, error_details)

    _print_report(total, counters, error_details)

    return 0


def main() -> int:
    """Entry-Point."""
    parser = argparse.ArgumentParser(
        description="Benchmark des GeocodingService gegen den Gold-Standard."
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Cache deaktivieren, um nur frische Heuristik-Ergebnisse zu testen.",
    )
    args = parser.parse_args()

    return benchmark(use_cache=not args.no_cache)


if __name__ == "__main__":
    sys.exit(main())
