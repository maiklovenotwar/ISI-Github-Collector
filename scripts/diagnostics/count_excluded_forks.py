#!/usr/bin/env python3
"""Ermittelt die Größenordnung der Forks, die nicht Teil der Sammlung sind.

Die Repository-Sammlung fragt die GitHub-Suche ohne `fork:`-Angabe ab. Die Suche liefert
Forks nur, wenn `fork:true` oder `fork:only` in der Abfrage steht - Forks gehören deshalb
nicht zur Grundgesamtheit. Dieses Skript fragt für mehrere Stern-Schwellen ab, wie viele
Forks und wie viele eigenständige Repositories GitHub jeweils meldet.

Je Schwelle zwei Suchanfragen mit einem Ergebnis pro Seite; ausgewertet wird nur die von
GitHub gemeldete Gesamtzahl (`total_count`). Diese Zahl ist laut GitHub ein Näherungswert.
Schreibt nichts, verwendet keine Datenbank und nicht den lokalen API-Cache.

Usage:
    python scripts/diagnostics/count_excluded_forks.py
    python scripts/diagnostics/count_excluded_forks.py --min-stars 10 --min-stars 100
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Tuple

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from github_collector.api.github_api import GitHubAPI  # noqa: E402
from github_collector.utils.secrets import SecretManager  # noqa: E402

DEFAULT_MIN_STARS = [1, 10, 100, 1000]


def build_queries(min_stars: List[int]) -> List[Tuple[int, str, str]]:
    """Je Schwelle zwei Abfragen: nur Forks und nur eigenständige Repositories."""
    queries = []
    for stars in min_stars:
        queries.append((stars, "forks", f"stars:>={stars} fork:only"))
        queries.append((stars, "non_forks", f"stars:>={stars}"))
    return queries


def count(api, query: str) -> Tuple[int, bool]:
    """Gesamtzahl der Treffer und ob GitHub die Antwort als unvollständig markiert hat."""
    result = api.search_repositories(query=query, per_page=1, page=1)
    return result.get("total_count", 0), bool(result.get("incomplete_results", False))


def parse_arguments(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Zählt Forks und eigenständige Repositories je Stern-Schwelle (GitHub-Suche)"
    )
    parser.add_argument(
        "--min-stars",
        type=int,
        action="append",
        help=f"Stern-Schwelle, mehrfach angebbar (default: {DEFAULT_MIN_STARS})",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None, api=None) -> int:
    args = parse_arguments(argv)
    thresholds = args.min_stars or DEFAULT_MIN_STARS
    if api is None:
        # Ohne cache_dir: die Zahlen sollen frisch von GitHub kommen
        api = GitHubAPI(SecretManager.get_github_tokens())

    counts = {}
    incomplete = False
    for stars, kind, query in build_queries(thresholds):
        total, flagged = count(api, query)
        counts[(stars, kind)] = total
        incomplete = incomplete or flagged

    print(f"{'Stars >=':>9} {'Forks':>14} {'eigenständig':>14} {'Forks je 100 eigenständige':>28}")
    for stars in thresholds:
        forks, non_forks = counts[(stars, "forks")], counts[(stars, "non_forks")]
        ratio = f"{forks / non_forks * 100:.1f}" if non_forks else "-"
        print(f"{stars:>9,} {forks:>14,} {non_forks:>14,} {ratio:>28}")
    print("\nHinweis: total_count der GitHub-Suche ist ein Näherungswert.")
    if incomplete:
        print("Hinweis: GitHub hat mindestens eine Antwort als unvollständig markiert.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
