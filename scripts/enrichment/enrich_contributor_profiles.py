#!/usr/bin/env python3
"""Reichert `contributors`-Zeilen mit Profildaten (name, location, company etc.) an.

Bearbeitet nur Contributors, deren Profil noch nie abgefragt wurde
(`enrichment_checked_at IS NULL`), und vermerkt jeden Abrufversuch in dieser Spalte -
auch wenn GitHub das Konto nicht mehr kennt oder das Profil leer ist.

--scope all     : alle noch nicht abgefragten Contributors (Standard)
--scope new     : nur Contributors, die über repository_contributors bekannt wurden
                  (Mitwirkende an Repositories)
--scope gap     : alle anderen (in der Regel Repository-Besitzer)

Mehrere Prozesse können parallel disjunkte Teilmengen bearbeiten, jeweils mit einem
eigenen Token:

    python scripts/enrichment/enrich_contributor_profiles.py --token-index 0 --shard-index 0 --shard-count 2
    python scripts/enrichment/enrich_contributor_profiles.py --token-index 1 --shard-index 1 --shard-count 2

Die Logik liegt in src/github_collector/enrichment/contributor_profiles.py.
"""

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import requests  # noqa: E402
from tqdm import tqdm  # noqa: E402

from github_collector import config  # noqa: E402
from github_collector.database.engine import make_engine  # noqa: E402
from github_collector.enrichment.contributor_profiles import (  # noqa: E402
    BATCH_SIZE,
    SCOPES,
    flush_rows,
    get_candidates,
    process_batch,
)
from github_collector.utils.secrets import SecretManager  # noqa: E402

LOG_FILE = config.LOG_DIR / "enrich_contributor_profiles.log"
logger = logging.getLogger(__name__)


def parse_arguments(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Contributor-Profile (location etc.) anreichern")
    parser.add_argument(
        "--scope",
        choices=SCOPES,
        default="all",
        help="new: Mitwirkende aus repository_contributors; gap: alle anderen; all: beide",
    )
    parser.add_argument("--token-index", type=int, default=0, help="Index in GITHUB_API_TOKENS")
    parser.add_argument("--shard-index", type=int, default=0, help="Nummer dieses Teilprozesses")
    parser.add_argument("--shard-count", type=int, default=1, help="Anzahl paralleler Prozesse")
    parser.add_argument("--limit", type=int, default=None, help="Max. Anzahl (Test-Zwecke)")
    parser.add_argument(
        "--dry-run", action="store_true", help="Profile abrufen, aber nichts schreiben"
    )
    args = parser.parse_args(argv)
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error("--shard-index muss zwischen 0 und --shard-count - 1 liegen.")
    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_arguments(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")],
    )

    tokens = SecretManager.get_github_tokens()
    if args.token_index >= len(tokens):
        logger.error(
            "token-index %d, aber nur %d Tokens verfuegbar.", args.token_index, len(tokens)
        )
        return 1
    token = tokens[args.token_index]

    engine = make_engine()
    candidates = get_candidates(engine, args.scope, args.shard_index, args.shard_count)
    if args.limit:
        candidates = candidates[: args.limit]
    logger.info(
        "scope=%s shard=%d/%d token_index=%d: %d Contributors ausgewaehlt.",
        args.scope,
        args.shard_index,
        args.shard_count,
        args.token_index,
        len(candidates),
    )
    if not candidates:
        return 0

    session = requests.Session()
    buffer: List[Dict[str, Any]] = []
    not_found = 0
    parsed = 0
    start = time.time()
    batches = [candidates[i : i + BATCH_SIZE] for i in range(0, len(candidates), BATCH_SIZE)]

    with tqdm(total=len(batches), desc=f"shard{args.shard_index}") as pbar:
        for batch in batches:
            batch_not_found, batch_parsed = process_batch(
                session, token, engine, batch, buffer, args.dry_run
            )
            not_found += batch_not_found
            parsed += batch_parsed
            pbar.update(1)

    # Der Rest-Puffer (kleiner als FLUSH_SIZE, daher noch nicht geschrieben).
    if buffer and not args.dry_run:
        flush_rows(engine, buffer)

    duration = time.time() - start
    logger.info(
        "Fertig%s: %d Profile abgerufen, %d nicht gefunden (geloescht/umbenannt), "
        "%.1f min (%.0f/h)",
        " (DRY-RUN, nichts geschrieben)" if args.dry_run else "",
        parsed,
        not_found,
        duration / 60,
        parsed / (duration / 3600) if duration else 0,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
