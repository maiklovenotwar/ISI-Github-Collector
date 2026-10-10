#!/usr/bin/env python3
"""Orchestriert die komplette Contributor-Netzwerk-Sammlung für einen Star-Bereich.

Führt die vier Schritte nacheinander aus:

    1. collect_repository_contributors.py  --min-stars --max-stars
    2. add_missing_contributors.py
    3. scripts/enrichment/enrich_contributor_profiles.py --scope new   (parallel geshardet
       über alle verfügbaren GitHub-Tokens)
    4. scripts/enrichment/update_location_geocoding.py --contributors

Jeder Schritt ist einzeln bereits resumable/idempotent (siehe deren Docstrings);
dieses Skript fügt nur die Reihenfolge hinzu und leitet die Token-Anzahl für das
Sharding automatisch her, statt sie von Hand zu wiederholen.

--skip-* erlaubt, einzelne Schritte auszulassen (z.B. wenn die Sammlung schon
lief und nur die Anreicherung nachgeholt werden soll).

Die Ausgabe jedes Schritts wird in eine eigene Datei im Log-Verzeichnis geschrieben.
"""

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.stdout.reconfigure(encoding="utf-8")
SCRIPTS_DIR = Path(__file__).parent.parent

sys.path.insert(0, str(SCRIPTS_DIR.parent / "src"))

from github_collector import config  # noqa: E402
from github_collector.utils.secrets import SecretManager  # noqa: E402

STEP_SCRIPTS: Dict[str, Path] = {
    "collect": SCRIPTS_DIR / "collection" / "collect_repository_contributors.py",
    "add_missing": SCRIPTS_DIR / "collection" / "add_missing_contributors.py",
    "enrich": SCRIPTS_DIR / "enrichment" / "enrich_contributor_profiles.py",
    "geocode": SCRIPTS_DIR / "enrichment" / "update_location_geocoding.py",
}
# Dateinamen-Präfix der Schritt-Ausgabe im Log-Verzeichnis
LOG_PREFIXES: Dict[str, str] = {
    "collect": "collect",
    "add_missing": "add_missing",
    "enrich": "enrich_profiles",
    "geocode": "geocoding",
}

LOG_FILE = config.LOG_DIR / "collect_contributor_network.log"
logger = logging.getLogger(__name__)


def cohort_label(min_stars: int, max_stars: Optional[int]) -> str:
    if max_stars:
        return f"{min_stars}to{max_stars}stars"
    return f"{min_stars}stars"


def log_path(step: str, label: str, shard: Optional[int] = None) -> Path:
    """Datei, in die die Ausgabe eines Schritts (bzw. eines Shards) geschrieben wird."""
    suffix = f"_shard{shard}" if shard is not None else ""
    return config.LOG_DIR / f"{LOG_PREFIXES[step]}_{label}{suffix}.log"


def run(cmd: List[str], log_file: Optional[Path] = None) -> int:
    """Führt einen Schritt aus, streamt nach stdout oder stattdessen in eine Log-Datei."""
    logger.info("Starte: %s", " ".join(cmd))
    if log_file is None:
        result = subprocess.run(cmd)
        return result.returncode
    with open(log_file, "w", encoding="utf-8") as f:
        result = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    return result.returncode


def run_parallel(cmds: List[List[str]], log_files: List[Path]) -> List[int]:
    """Startet mehrere Kommandos parallel (für die geshardete Anreicherung) und wartet auf alle."""
    handles = []
    files = []
    for cmd, log_file in zip(cmds, log_files):
        logger.info("Starte parallel: %s", " ".join(cmd))
        f = open(log_file, "w", encoding="utf-8")
        files.append(f)
        handles.append(subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT))
    return_codes = [h.wait() for h in handles]
    for f in files:
        f.close()
    return return_codes


def enrich_commands(py: str, token_count: int) -> List[List[str]]:
    """Ein Anreicherungs-Kommando pro Token; jedes bearbeitet seinen eigenen Shard."""
    return [
        [
            py,
            str(STEP_SCRIPTS["enrich"]),
            "--scope",
            "new",
            "--token-index",
            str(i),
            "--shard-index",
            str(i),
            "--shard-count",
            str(token_count),
        ]
        for i in range(token_count)
    ]


def stage_collect(args, label: str, py: str) -> int:
    """Schritt 1."""
    cmd = [
        py,
        str(STEP_SCRIPTS["collect"]),
        "--min-stars",
        str(args.min_stars),
        "--max-pages",
        str(args.max_pages),
    ]
    if args.max_stars:
        cmd += ["--max-stars", str(args.max_stars)]
    if args.limit:
        cmd += ["--limit", str(args.limit)]
    if args.dry_run:
        cmd += ["--dry-run"]
    rc = run(cmd, log_path("collect", label))
    if rc != 0:
        logger.error("Sammlung fehlgeschlagen (exit %d), breche Pipeline ab.", rc)
    return rc


def stage_add_missing(label: str, py: str) -> int:
    """Schritt 2."""
    rc = run([py, str(STEP_SCRIPTS["add_missing"])], log_path("add_missing", label))
    if rc != 0:
        logger.error("Stub-Anlage fehlgeschlagen (exit %d), breche Pipeline ab.", rc)
    return rc


def stage_enrich(label: str, py: str) -> int:
    """Schritt 3: Profil-Anreicherung, geshardet über alle konfigurierten Tokens."""
    try:
        n = len(SecretManager.get_github_tokens())
    except ValueError as e:
        logger.error("Kein GitHub-Token verfuegbar, Profil-Anreicherung nicht moeglich: %s", e)
        return 1
    if n == 0:
        logger.error("Kein GitHub-Token konfiguriert, Profil-Anreicherung nicht moeglich.")
        return 1
    logger.info("%d Tokens gefunden, sharde Profil-Anreicherung entsprechend.", n)
    return_codes = run_parallel(
        enrich_commands(py, n), [log_path("enrich", label, i) for i in range(n)]
    )
    failed = [i for i, rc in enumerate(return_codes) if rc != 0]
    if failed:
        logger.error(
            "Shards fehlgeschlagen: %s. Log-Dateien pruefen, dann erneut aufrufen "
            "(idempotent) - z.B. mit --skip-collect --skip-stubs.",
            failed,
        )
        return 1
    return 0


def stage_geocode(label: str, py: str) -> int:
    """Schritt 4."""
    rc = run(
        [
            py,
            str(STEP_SCRIPTS["geocode"]),
            "--contributors",
            "--no-interactive-filters",
            "--yes",
        ],
        log_path("geocode", label),
    )
    if rc != 0:
        logger.error("Geocoding fehlgeschlagen (exit %d).", rc)
    return rc


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Orchestriert Sammlung + Anreicherung + Geocoding für einen Star-Bereich"
    )
    parser.add_argument("--min-stars", type=int, required=True, help="Star-Untergrenze")
    parser.add_argument(
        "--max-stars", type=int, default=None, help="Star-Obergrenze, exklusiv (default: offen)"
    )
    parser.add_argument("--max-pages", type=int, default=20, help="Siehe collect-Skript")
    parser.add_argument("--limit", type=int, default=None, help="An collect durchgereicht (Tests)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur Schritt 1 im Dry-Run, dann Stop; die schreibenden Schritte 2-4 laufen nie",
    )
    parser.add_argument("--skip-collect", action="store_true", help="Schritt 1 auslassen")
    parser.add_argument(
        "--skip-stubs", action="store_true", help="Schritt 2 (add_missing_contributors) auslassen"
    )
    parser.add_argument("--skip-enrich", action="store_true", help="Schritt 3 auslassen")
    parser.add_argument("--skip-geocode", action="store_true", help="Schritt 4 auslassen")
    args = parser.parse_args(argv)
    if args.max_stars is not None and args.max_stars <= args.min_stars:
        parser.error("--max-stars muss groesser sein als --min-stars")
    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")],
    )
    label = cohort_label(args.min_stars, args.max_stars)
    py = sys.executable

    if not args.skip_collect:
        rc = stage_collect(args, label, py)
        if rc != 0:
            return rc
    else:
        logger.info("Ueberspringe Sammlung (--skip-collect).")

    # Die Schritte 2 bis 4 schreiben. Im Dry-Run werden sie nie gestartet, auch nicht, wenn
    # Schritt 1 mit --skip-collect ausgelassen wurde.
    if args.dry_run:
        logger.info("Dry-Run: stoppe nach Schritt 1, keine weiteren Schritte.")
        return 0

    if not args.skip_stubs:
        rc = stage_add_missing(label, py)
        if rc != 0:
            return rc
    else:
        logger.info("Ueberspringe Stub-Anlage (--skip-stubs).")

    if not args.skip_enrich:
        rc = stage_enrich(label, py)
        if rc != 0:
            return rc
    else:
        logger.info("Ueberspringe Profil-Anreicherung (--skip-enrich).")

    if not args.skip_geocode:
        rc = stage_geocode(label, py)
        if rc != 0:
            return rc
    else:
        logger.info("Ueberspringe Geocoding (--skip-geocode).")

    logger.info("Pipeline fuer Kohorte %s abgeschlossen.", label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
