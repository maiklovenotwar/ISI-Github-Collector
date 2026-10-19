#!/usr/bin/env python3
"""Zieht die Validierungsstichprobe aus der Ergebnisliste eines Orbis-Laufs (E20).

Eingabe ist der Export von `orbis_matching_pipeline.py --export-csv` (eine Zeile je
Organisation, auch ohne Ergebnis). Die Stichprobe ist geschichtet nach Ergebnis und wird
mit festem Seed ohne Zurücklegen gezogen; Treffer über die Umschrift und Treffer des
Namens-Rückfalls werden zusätzlich überstichprobt. Organisationen, an denen die Regeln
entwickelt wurden, werden ausgeschlossen. Ausgabe sind zwei Dateien: eine Blind-Datei
(nur GitHub-Angaben, für die erste Prüfstufe) und eine Schlüsseldatei mit dem Ergebnis
des Verfahrens. Beide enthalten Orbis-Daten bzw. verweisen darauf und dürfen nur in von
Git ignorierte Pfade (Protokoll: docs/methodology/manual_validation.md).

Usage:
    python scripts/diagnostics/draw_orbis_validation_sample.py \\
        --export workstreams/orbis_matching/data/run.csv \\
        --out workstreams/orbis_matching/data/round1 \\
        --exclude-logins workstreams/orbis_matching/data/development_logins.txt
"""

import argparse
import csv
import random
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from github_collector.utils.safe_output import require_ignored_path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SEED = 20261018

# Gezogene Schichten und ihre Größe; "not_searchable" wird nur gezählt.
DEFAULT_SIZES: Dict[str, int] = {
    "unique": 50,
    "consensus": 50,
    "pending_resolved": 50,
    "pending_unresolved": 100,
    "searched_no_candidate": 100,
}
# Überstichprobe aus den übrigen Fällen, jeweils höchstens so viele.
OVERSAMPLE_SIZE = 50
BLIND_COLUMNS = ["case_id", "login", "name", "blog", "github_country"]


def stratum_of(row: Dict[str, str]) -> str:
    """Schicht einer Organisation aus Status, Ergebnis und Suchweg."""
    status = row["status"]
    if status in ("unique", "consensus"):
        return status
    if status == "pending":
        return "pending_resolved" if row["bvd_id"] else "pending_unresolved"
    if row["search_path"] == "not_searchable":
        return "not_searchable"
    return "searched_no_candidate"


def _oversample_group(row: Dict[str, str]) -> Optional[str]:
    if not row["bvd_id"]:
        return None
    if row.get("via_transliteration") == "True":
        return "via_transliteration"
    if row.get("match_type") in ("name_only", "login_fallback"):
        return "name_or_login_fallback"
    return None


def draw_sample(
    rows: Sequence[Dict[str, str]],
    sizes: Optional[Dict[str, int]] = None,
    seed: int = DEFAULT_SEED,
    exclude_logins: Optional[Set[str]] = None,
) -> List[Dict[str, str]]:
    """Geschichtete Stichprobe; jede gezogene Zeile trägt `stratum` (Schicht oder Überstichprobe)."""
    sizes = DEFAULT_SIZES if sizes is None else sizes
    excluded = exclude_logins or set()
    rng = random.Random(seed)
    by_stratum: Dict[str, List[Dict[str, str]]] = {}
    for row in rows:
        if row["login"] in excluded:
            continue
        by_stratum.setdefault(stratum_of(row), []).append(row)

    sample: List[Dict[str, str]] = []
    drawn: Set[str] = set()
    for stratum in sorted(sizes):
        pool = by_stratum.get(stratum, [])
        for row in rng.sample(pool, min(sizes[stratum], len(pool))):
            sample.append({**row, "stratum": stratum})
            drawn.add(row["login"])

    remaining: Dict[str, List[Dict[str, str]]] = {}
    for stratum_rows in by_stratum.values():
        for row in stratum_rows:
            group = _oversample_group(row)
            if group and row["login"] not in drawn:
                remaining.setdefault(group, []).append(row)
    for group in sorted(remaining):
        pool = remaining[group]
        for row in rng.sample(pool, min(OVERSAMPLE_SIZE, len(pool))):
            sample.append({**row, "stratum": f"oversample_{group}"})
    # Gemischt, damit die Fallnummer in der Blind-Datei die Schicht nicht verrät.
    rng.shuffle(sample)
    return sample


def write_sample(sample: List[Dict[str, str]], out_stem: Path) -> Tuple[Path, Path]:
    """Schreibe `<stem>_blind.csv` und `<stem>_key.csv`; Fallnummern in Zufallsreihenfolge."""
    out_stem.parent.mkdir(parents=True, exist_ok=True)
    blind_path = out_stem.with_name(out_stem.name + "_blind.csv")
    key_path = out_stem.with_name(out_stem.name + "_key.csv")
    numbered = [{**row, "case_id": f"C{index:04d}"} for index, row in enumerate(sample, 1)]
    with open(blind_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=BLIND_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(numbered)
    key_columns = ["case_id", "stratum"] + [
        c for c in (numbered[0] if numbered else {}) if c not in ("case_id", "stratum")
    ]
    with open(key_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=key_columns)
        writer.writeheader()
        writer.writerows(numbered)
    return blind_path, key_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validierungsstichprobe ziehen")
    parser.add_argument("--export", required=True, help="CSV aus --export-csv eines Laufs")
    parser.add_argument("--out", required=True, help="Pfad-Stamm der Ausgabedateien")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--exclude-logins", help="Datei mit einem Login je Zeile")
    args = parser.parse_args(argv)

    out_stem = require_ignored_path(Path(args.out).resolve(), REPO_ROOT)
    with open(args.export, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    excluded: Set[str] = set()
    if args.exclude_logins:
        excluded = {
            line.strip()
            for line in Path(args.exclude_logins).read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    counts: Dict[str, int] = {}
    for row in rows:
        counts[stratum_of(row)] = counts.get(stratum_of(row), 0) + 1
    print("Organisationen je Schicht:", dict(sorted(counts.items())))

    sample = draw_sample(rows, seed=args.seed, exclude_logins=excluded)
    blind, key = write_sample(sample, out_stem)
    print(f"{len(sample)} Fälle gezogen (Seed {args.seed}): {blind}, {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
