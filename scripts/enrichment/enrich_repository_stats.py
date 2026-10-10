"""Orchestrierungsskript für die Anreicherung von Repository-Statistiken via GitHub GraphQL API.

- Holt zu aktualisierende Repos aus der DB
- Ruft den GraphQLHandler in Batches auf
- Übergibt Ergebnisse an updater.py
- Unterstützt --dry-run, --limit, --batch-size, --force
- Zeigt Fortschritt und Zusammenfassung

GitHub-PATs werden über den SecretManager geladen (z.B. aus GITHUB_API_TOKENS, Keyring oder sicherer Eingabe).
"""

import argparse
import os
import sys

from sqlalchemy import create_engine, text
from tqdm import tqdm

from github_collector import config
from github_collector.enrichment.graphql_handler import GraphQLHandler
from github_collector.enrichment.updater import map_and_update_stats
from github_collector.utils.logging_config import setup_logging

# Einheitliches Logging: Schreibe in eigene Logdatei im logs/-Ordner
ENRICH_LOG = config.LOG_DIR / "enrich_repository_stats.log"
logger = setup_logging(log_file=ENRICH_LOG)


def get_repos_to_enrich(db_url, limit=None, force=False):
    """
    Holt Repos aus der DB, die angereichert werden sollen.
    :param db_url: SQLAlchemy-DB-URL
    :param limit: maximale Anzahl
    :param force: Wenn True, ignoriere NULL-Check (alle Repos)
    :return: Liste von Dicts mit id, owner, name
    """
    engine = create_engine(db_url)
    with engine.connect() as conn:
        if force:
            query = text(
                """
                SELECT r.id, COALESCE(o.login, c.login) as owner, r.name
                FROM repositories r
                LEFT JOIN organizations o ON r.owner_id = o.id
                LEFT JOIN contributors c ON r.owner_id = c.id
            """
            )
            params = {}
        else:
            query = text(
                """
                SELECT r.id, COALESCE(o.login, c.login) as owner, r.name
                FROM repositories r
                LEFT JOIN organizations o ON r.owner_id = o.id
                LEFT JOIN contributors c ON r.owner_id = c.id
                WHERE r.contributors_count IS NULL OR r.commits_count IS NULL OR r.pull_requests_count IS NULL
            """
            )
            params = {}
        if limit:
            query = text(str(query) + " LIMIT :limit")
            params["limit"] = limit
        result = conn.execute(query, params)
        rows = result.fetchall()
    return [{"id": r[0], "owner": r[1], "name": r[2]} for r in rows]


def main():  # noqa: C901
    """
    Einstiegspunkt für das Enrichment der Repository-Statistiken.

    Parsed die Kommandozeilenargumente, lädt die zu verarbeitenden Repositories,
    ruft den GraphQLHandler für die Anreicherung auf und aktualisiert die Datenbank.
    Unterstützt Checkpointing (Fortsetzung ab Abbruch), Retry-Logik für fehlgeschlagene Repositories
    und schreibt alle relevanten Informationen in das Logfile `logs/enrich_repository_stats.log`.

    Besonderheiten:
    - contributors_count wird aktuell nicht gesammelt (Performance-Limit der API)
    - commits_count bezieht sich auf den Default Branch
    """
    parser = argparse.ArgumentParser(description="Enrich repository stats via GitHub GraphQL API")
    parser.add_argument(
        "--db-path", required=False, help="Path to SQLite DB (default: data/github_data.db)"
    )
    parser.add_argument(
        "--batch-size", type=int, default=50, help="Batch size for GraphQL queries (default: 50)"
    )
    parser.add_argument("--limit", type=int, default=None, help="Max number of repos to process")
    parser.add_argument(
        "--force", action="store_true", help="Process all repos, not just those with NULL counts"
    )
    parser.add_argument("--dry-run", action="store_true", help="Run without writing to DB")
    parser.add_argument(
        "--retry-failed",
        type=str,
        default=None,
        help="Pfad zu einer Datei mit Repo-IDs (eine pro Zeile) für gezielten Retry-Lauf",
    )
    args = parser.parse_args()

    # Ermittle Datenbank-URL wie in den anderen Skripten: bevorzugt --db-path, sonst zentrale DB_URL
    db_url = args.db_path or str(config.DB_URL)
    # Falls ein expliziter Pfad übergeben wurde, der keine SQLAlchemy-URL ist, baue eine SQLite-URL daraus
    if args.db_path and isinstance(db_url, str) and "://" not in db_url:
        db_url = f"sqlite:///{os.path.abspath(db_url)}"

    logger.info("Starte Enrichment-Prozess...")
    if args.retry_failed:
        # Nur die angegebenen IDs verarbeiten
        retry_path = args.retry_failed
        if not os.path.exists(retry_path):
            logger.error(f"Retry-Datei nicht gefunden: {retry_path}")
            sys.exit(1)
        with open(retry_path, "r") as f:
            retry_ids = {line.strip() for line in f if line.strip()}
        # Hole alle Repos aus DB, filtere auf die IDs
        all_repos = get_repos_to_enrich(db_url, limit=None, force=True)
        repos = [r for r in all_repos if str(r["id"]) in retry_ids]
        logger.info(f"{len(repos)} Repositories aus Retry-Datei für Enrichment ausgewählt.")
    else:
        repos = get_repos_to_enrich(db_url, limit=args.limit, force=args.force)
        logger.info(f"{len(repos)} Repositories für Enrichment ausgewählt.")
    if not repos:
        logger.info("Keine Repositories zum Anreichern gefunden. Beende.")
        sys.exit(0)

    handler = GraphQLHandler(batch_size=args.batch_size)
    all_stats = []
    failed_batches = []
    batches = [repos[i : i + args.batch_size] for i in range(0, len(repos), args.batch_size)]

    import time

    batch_durations = []
    batch_success_counts = []
    batch_fail_counts = []
    batch_indices = []
    with tqdm(total=len(batches), desc="GraphQL-Batches") as pbar:
        for idx, batch in enumerate(batches):
            batch_start = time.time()
            try:
                stats, batch_fails = handler.fetch_repo_stats(batch)
                all_stats.extend(stats)
                if batch_fails:
                    failed_batches.extend(batch_fails)
                batch_success_counts.append(len(stats))
                batch_fail_counts.append(len(batch_fails) if batch_fails else 0)
            except Exception as e:
                logger.error(f"Fehler in Batch {idx+1}/{len(batches)}: {e}")
                failed_batches.append(batch)
                batch_success_counts.append(0)
                batch_fail_counts.append(len(batch))
            batch_duration = time.time() - batch_start
            batch_durations.append(batch_duration)
            batch_indices.append(idx)
            logger.info(
                f"Batch {idx+1}/{len(batches)}: Dauer {batch_duration:.2f}s, Erfolge: {batch_success_counts[-1]}, Fehler: {batch_fail_counts[-1]}"
            )
            pbar.update(1)
    logger.info(f"{len(all_stats)} Repositories erfolgreich via GraphQL abgefragt.")
    if failed_batches:
        logger.warning(f"{len(failed_batches)} Batches konnten nicht verarbeitet werden.")

    # Mapping: database_id (API) <-> id (DB).
    # Wichtig: Die "repo_id" aus der API ist eine Node-ID (Base64-String), die DB nutzt aber die numerische database_id/id.
    stats_for_update = []
    db_id_set = {r["id"] for r in repos}
    for stat in all_stats:
        if "database_id" in stat and stat["database_id"] in db_id_set:
            stats_for_update.append(stat)

    updated = map_and_update_stats(db_url, stats_for_update, dry_run=args.dry_run)

    logger.info("==== Zusammenfassung ====")
    logger.info(f"Repos ausgewählt: {len(repos)}")
    logger.info(f"Repos via GraphQL abgefragt: {len(all_stats)}")
    logger.info(f"Repos in DB aktualisiert: {updated}")
    logger.info(f"Fehlgeschlagene Batches: {len(failed_batches)}")

    # Performance-Statistiken
    if batch_durations:
        avg_duration = sum(batch_durations) / len(batch_durations)
        slowest = sorted(zip(batch_durations, batch_indices), reverse=True)[:3]
        logger.info(f"Durchschnittliche Batch-Dauer: {avg_duration:.2f}s")
        logger.info("Top 3 langsamste Batches:")
        for dur, idx in slowest:
            logger.info(f"  Batch {idx+1}: {dur:.2f}s")
        total_success = sum(batch_success_counts)
        total_fail = sum(batch_fail_counts)
        total = total_success + total_fail
        logger.info(
            f"Erfolgsquote: {total_success}/{total} ({(total_success/total*100 if total else 0):.1f}%)"
        )

    # Fehlgeschlagene Repo-IDs in Datei schreiben
    if failed_batches:
        from datetime import datetime

        today = datetime.now().strftime("%Y%m%d")
        fail_path = f"failed_repo_ids_{today}.txt"
        failed_ids = set()
        for batch in failed_batches:
            for r in batch:
                if "id" in r:
                    failed_ids.add(str(r["id"]))
                elif "repo_id" in r:
                    failed_ids.add(str(r["repo_id"]))
        with open(fail_path, "a") as f:
            for rid in sorted(failed_ids):
                f.write(rid + "\n")
        logger.info(f"Fehlgeschlagene Repo-IDs in {fail_path} gespeichert ({len(failed_ids)} IDs).")
        logger.info("IDs der ersten fehlgeschlagenen Batches:")
        for batch in failed_batches[:3]:
            ids = []
            for r in batch:
                if "repo_id" in r:
                    ids.append(r["repo_id"])
                elif "id" in r:
                    ids.append(r["id"])
                elif "name" in r and "owner" in r:
                    ids.append(f"{r['owner']}/{r['name']}")
                else:
                    ids.append(str(r))
            logger.info(ids)
    logger.info("Enrichment-Prozess abgeschlossen.")


if __name__ == "__main__":
    main()
