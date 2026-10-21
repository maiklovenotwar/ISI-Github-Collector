#!/usr/bin/env python3
"""Sammelt das Contributor-Netzwerk pro Repository via GitHub REST API.

Füllt die Tabelle `repository_contributors` (Migration:
scripts/migrations/add_repository_contributors_table.sql) für Repositories in
einem Star-Bereich (`--min-stars`, optional `--max-stars`). Wird üblicherweise
über collect_contributor_network.py für einen ganzen Stern-Bereich aufgerufen
(dort Schritt 1), kann aber auch direkt für gezielte Nachsammlungen verwendet werden.

Bulk-Writes (ein INSERT ... ON DUPLICATE KEY UPDATE pro Flush-Batch statt pro
Contributor) statt Einzelzeilen-Updates, da genau das in den bisherigen
GraphQL-Anreicherungsläufen (org_data_graphql.log, contributor_data_graphql.log)
der dominante Flaschenhals war, nicht das GitHub Rate Limit.

Geschrieben wird mit MySQL-spezifischem SQL; auf anderen Datenbanken läuft das Skript
nur mit `--dry-run`.
"""

import argparse
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from sqlalchemy import bindparam, text  # noqa: E402
from sqlalchemy.dialects.mysql import insert as mysql_insert  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402
from tqdm import tqdm  # noqa: E402

from github_collector import config  # noqa: E402
from github_collector.api.github_api import GitHubAPI, GitHubAPIError  # noqa: E402
from github_collector.database.engine import make_engine  # noqa: E402
from github_collector.database.models import RepositoryContributor  # noqa: E402
from github_collector.utils.retry import retry_with_backoff  # noqa: E402
from github_collector.utils.secrets import SecretManager  # noqa: E402

LOG_FILE = config.LOG_DIR / "collect_repository_contributors.log"
# Liste der Repos, deren Contributor-Abruf in einem Lauf fehlgeschlagen ist
FAILED_REPOS_DIR = config.LOG_DIR
logger = logging.getLogger(__name__)


def get_target_repos(
    engine,
    min_stars: int,
    max_stars: Optional[int],
    limit: Optional[int],
    force: bool,
) -> List[Dict[str, Any]]:
    """Holt Repos im Star-Bereich [min_stars, max_stars), die noch keine Contributor-Zeilen haben.

    max_stars=None bedeutet nach oben offen. Eine explizite Obergrenze grenzt eine Kohorte
    sauber ein, statt sich allein auf den NOT-EXISTS-Filter zu verlassen - sonst wiederholt
    ein --min-stars-Lauf mit ORDER BY stars DESC immer wieder Versuche an Repos, die in einer
    hoeheren, bereits abgeschlossenen Kohorte strukturell nie Contributor-Zeilen bekommen
    haben (GitHub verweigert die Contributor-Liste für einzelne sehr große Repositories,
    z.B. torvalds/linux).
    """
    with engine.connect() as conn:
        if force:
            where_extra = ""
        else:
            where_extra = (
                "AND NOT EXISTS (SELECT 1 FROM repository_contributors rc "
                "WHERE rc.repository_id = r.id)"
            )
        query = text(
            f"""
            SELECT r.id, r.name, COALESCE(o.login, c.login) AS owner
            FROM repositories r
            LEFT JOIN organizations o ON r.organization_id = o.id
            LEFT JOIN contributors c ON r.owner_id = c.id
            WHERE r.stargazers_count >= :min_stars
            {"AND r.stargazers_count < :max_stars" if max_stars else ""}
            {where_extra}
            ORDER BY r.stargazers_count DESC
            {"LIMIT :limit" if limit else ""}
            """
        )
        params: Dict[str, Any] = {"min_stars": min_stars}
        if max_stars:
            params["max_stars"] = max_stars
        if limit:
            params["limit"] = limit
        rows = conn.execute(query, params).fetchall()
    return [{"id": r[0], "name": r[1], "owner": r[2]} for r in rows if r[2]]


def fetch_all_contributors(
    api: GitHubAPI, owner: str, name: str, max_pages: int
) -> List[Dict[str, Any]]:
    """Paginiert durch die Contributors eines Repos (bis max_pages, Sampling bei riesigen Repos)."""
    contributors: List[Dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        batch = api.get_repository_contributors(owner, name, per_page=100, page=page)
        if not batch:
            break
        contributors.extend(batch)
        if len(batch) < 100:
            break
    return contributors


#  Spaltenlaengen aus models.py (RepositoryContributor) - defensiv einhalten, da
# GitHub-Freitextfelder gelegentlich laengere/kaputte Werte liefern als das Schema erwartet
# (siehe Absturz 2026-10-08: "Data too long for column 'github_type'" hat einen ueber
# 29 Tage laufenden Sammel-Lauf gekillt, weil der Fehler ungefangen bis main() durchschlug -
# analoger Fall zum 2026-08-10-Absturz bei twitter_username in enrichment/contributor_profiles.py).
_MAX_LEN = {"github_login": 255, "github_type": 20}


def _truncate(value: Optional[str], field: str) -> Optional[str]:
    if value is None:
        return None
    max_len = _MAX_LEN[field]
    if len(value) > max_len:
        logger.warning("Feld %s zu lang (%d Zeichen), kuerze auf %d.", field, len(value), max_len)
        return value[:max_len]
    return value


def contributor_rows(
    repository_id: int, contributors: List[Dict[str, Any]], collected_at: datetime
) -> List[Dict[str, Any]]:
    """Übersetzt die API-Antwort für ein Repository in Zeilen für repository_contributors.

    Anonyme Commit-Autoren haben in der API-Antwort keinen Login und keine ID; sie erhalten
    den Ersatz-Login `anon:<email oder name>` und bleiben ohne github_user_id.
    """
    rows: List[Dict[str, Any]] = []
    for c in contributors:
        login = c.get("login")
        is_anon = login is None
        github_login = login or f"anon:{c.get('email') or c.get('name') or 'unknown'}"
        rows.append(
            {
                "repository_id": repository_id,
                "github_login": _truncate(github_login, "github_login"),
                "github_user_id": c.get("id") if not is_anon else None,
                "github_type": _truncate(c.get("type"), "github_type") if not is_anon else None,
                "contributions": c.get("contributions"),
                "is_anonymous": is_anon,
                "collected_at": collected_at,
            }
        )
    return rows


@retry_with_backoff(
    max_attempts=5, initial_delay=2.0, backoff_factor=2.0, exceptions=OperationalError
)
def resolve_contributor_ids(engine, github_user_ids: List[int]) -> set:
    """Welche der gesehenen GitHub-User-IDs existieren bereits als Zeile in `contributors`?

    Matching erfolgt über die stabile numerische GitHub-ID (== contributors.id, siehe
    database.py:insert_contributor), nicht über den Login-String - der überlebt
    Account-Umbenennungen nicht. Nur existierende IDs dürfen als contributor_id gesetzt
    werden (FK-Constraint), daher der Existenz-Check vor jedem Schreibvorgang.
    """
    if not github_user_ids:
        return set()
    stmt = text("SELECT id FROM contributors WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    with engine.connect() as conn:
        rows = conn.execute(stmt, {"ids": github_user_ids})
        return {cid for (cid,) in rows}


@retry_with_backoff(
    max_attempts=5, initial_delay=2.0, backoff_factor=2.0, exceptions=OperationalError
)
def _write_batch(engine, stmt) -> None:
    with engine.begin() as conn:
        conn.execute(stmt)


def upsert_statement(rows: List[Dict[str, Any]]):
    """Bulk-Upsert (MySQL) auf dem Unique-Key (repository_id, github_login)."""
    stmt = mysql_insert(RepositoryContributor.__table__).values(rows)
    return stmt.on_duplicate_key_update(
        contributor_id=stmt.inserted.contributor_id,
        github_user_id=stmt.inserted.github_user_id,
        github_type=stmt.inserted.github_type,
        contributions=stmt.inserted.contributions,
        is_anonymous=stmt.inserted.is_anonymous,
        collected_at=stmt.inserted.collected_at,
        run_id=stmt.inserted.run_id,
    )


def _reason(error: Exception) -> str:
    """Fehlertext ohne die Parameter der Anweisung (sie enthalten Logins und E-Mail-Adressen)."""
    orig = getattr(error, "orig", None)  # Meldung des Datenbanktreibers, z. B. "Data too long"
    return str(orig) if orig is not None else type(error).__name__


def flush_rows(engine, rows: List[Dict[str, Any]]) -> int:
    """Schreibt die Zeilen und gibt die Anzahl der geschriebenen zurück.

    Schlägt der gemeinsame Schreibvorgang fehl, wird Zeile für Zeile geschrieben, damit ein
    einzelner unzulässiger Wert nicht den ganzen Batch verwirft (analog zu
    enrichment/contributor_profiles.py:flush_rows). Ein Repo, von dem gar keine Zeile
    geschrieben wurde, wählt der nächste Lauf über den NOT-EXISTS-Filter erneut aus. Ein Repo,
    von dem nur ein Teil geschrieben wurde, wählt er nicht mehr aus (der Filter fragt nur, ob es
    überhaupt eine Zeile gibt); solche Repos werden als unvollständig geloggt und lassen sich mit
    `--force` nachholen.
    """
    if not rows:
        return 0
    try:
        _write_batch(engine, upsert_statement(rows))
        return len(rows)
    except OperationalError:
        raise  # Verbindung weg: _write_batch hat schon wiederholt, einzeln hilft nicht
    except Exception as e:
        logger.error(
            "Flush von %d Zeilen fehlgeschlagen, schreibe einzeln: %s", len(rows), _reason(e)
        )

    written = 0
    written_repos, failed_repos = set(), set()
    for position, row in enumerate(rows, start=1):
        try:
            _write_batch(engine, upsert_statement([row]))
            written += 1
            written_repos.add(row["repository_id"])
        except Exception as e:
            failed_repos.add(row["repository_id"])
            # Ohne Login: bei anonymen Autoren ist er deren E-Mail-Adresse oder Name.
            logger.error(
                "Zeile %d fuer Repo %s nicht schreibbar: %s",
                position,
                row["repository_id"],
                _reason(e),
            )
    incomplete = sorted(written_repos & failed_repos)
    if incomplete:
        logger.error(
            "Repos unvollständig, werden nicht erneut ausgewählt (nachholen mit --force): %s",
            ", ".join(str(r) for r in incomplete),
        )
    return written


def flush_buffer(engine, buffer: List[Dict[str, Any]], run_id: str, dry_run: bool) -> int:
    """Löst Contributor-IDs auf und schreibt den Buffer als einen Bulk-Upsert."""
    if not buffer:
        return 0

    candidate_ids = sorted(
        {row["github_user_id"] for row in buffer if row["github_user_id"] is not None}
    )
    existing_ids = resolve_contributor_ids(engine, candidate_ids)
    for row in buffer:
        row["contributor_id"] = (
            row["github_user_id"] if row["github_user_id"] in existing_ids else None
        )
        row["run_id"] = run_id

    if dry_run:
        logger.info("DRY-RUN: %d Contributor-Zeilen wuerden geschrieben.", len(buffer))
        return len(buffer)

    return flush_rows(engine, buffer)


def parse_arguments(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sammle Repository-Contributor-Netzwerk via REST API"
    )
    parser.add_argument(
        "--min-stars", type=int, default=5000, help="Star-Untergrenze (default 5000)"
    )
    parser.add_argument(
        "--max-stars", type=int, default=None, help="Star-Obergrenze, exklusiv (default: offen)"
    )
    parser.add_argument("--limit", type=int, default=None, help="Max. Anzahl Repos (Test-Zwecke)")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=20,
        help="Max. Contributor-Seiten pro Repo (Sampling-Cap bei riesigen Repos, default 20=2000)",
    )
    parser.add_argument(
        "--flush-size",
        type=int,
        default=500,
        help="Anzahl Contributor-Zeilen pro gebuendeltem Bulk-Upsert",
    )
    parser.add_argument(
        "--force", action="store_true", help="Bereits erfasste Repos erneut sammeln"
    )
    parser.add_argument("--dry-run", action="store_true", help="Ohne DB-Schreibvorgaenge")
    args = parser.parse_args(argv)
    if args.max_stars is not None and args.max_stars <= args.min_stars:
        parser.error("--max-stars muss groesser sein als --min-stars")
    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_arguments(argv)

    engine = make_engine(str(config.DB_URL))
    if engine.dialect.name != "mysql" and not args.dry_run:
        print(
            "Dieses Skript schreibt mit MySQL-spezifischem SQL und unterstuetzt nur MySQL "
            f"(konfiguriert: {engine.dialect.name}). Mit --dry-run laeuft es ohne "
            "Schreibvorgaenge.",
            file=sys.stderr,
        )
        return 2

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")],
    )

    # Schema-Check: Migration muss vorher manuell gelaufen sein (siehe Modulkommentar).
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT 1 FROM repository_contributors LIMIT 1"))
        except Exception:
            logger.error(
                "Tabelle 'repository_contributors' nicht gefunden. Zuerst "
                "scripts/migrations/add_repository_contributors_table.sql ausfuehren."
            )
            return 1

    run_id = str(uuid.uuid4())
    logger.info(
        "Sammel-Lauf gestartet. run_id=%s, min_stars=%d, max_stars=%s",
        run_id,
        args.min_stars,
        args.max_stars,
    )

    tokens = SecretManager.get_github_tokens()
    api = GitHubAPI(tokens, cache_dir=str(config.CACHE_DIR))

    repos = get_target_repos(engine, args.min_stars, args.max_stars, args.limit, args.force)
    logger.info("%d Repos fuer diesen Lauf ausgewaehlt.", len(repos))
    if not repos:
        logger.info("Nichts zu tun. Beende.")
        return 0

    buffer: List[Dict[str, Any]] = []
    written_total = 0
    failed_repos: List[str] = []
    request_count = 0
    repo_durations: List[float] = []
    run_start = time.time()

    with tqdm(total=len(repos), desc="Repos") as pbar:
        for repo in repos:
            repo_start = time.time()
            try:
                raw_contributors = fetch_all_contributors(
                    api, repo["owner"], repo["name"], args.max_pages
                )
                request_count += max(1, -(-len(raw_contributors) // 100))
                buffer.extend(
                    contributor_rows(repo["id"], raw_contributors, datetime.now(timezone.utc))
                )
            except GitHubAPIError as e:
                logger.warning("Fehler bei %s/%s: %s", repo["owner"], repo["name"], e)
                failed_repos.append(f"{repo['owner']}/{repo['name']}")
            except Exception as e:
                logger.error(
                    "Unerwarteter Fehler bei %s/%s: %s",
                    repo["owner"],
                    repo["name"],
                    e,
                    exc_info=True,
                )
                failed_repos.append(f"{repo['owner']}/{repo['name']}")

            if len(buffer) >= args.flush_size:
                written_total += flush_buffer(engine, buffer, run_id, args.dry_run)
                buffer = []

            repo_durations.append(time.time() - repo_start)
            pbar.update(1)

    written_total += flush_buffer(engine, buffer, run_id, args.dry_run)
    run_duration = time.time() - run_start

    logger.info("==== Zusammenfassung Sammel-Lauf %s ====", run_id)
    logger.info("Repos verarbeitet: %d (davon fehlgeschlagen: %d)", len(repos), len(failed_repos))
    logger.info("Contributor-Zeilen geschrieben: %d", written_total)
    logger.info("REST-Requests (Naeherung, 1 pro angefangene 100er-Seite): %d", request_count)
    logger.info(
        "Laufzeit gesamt: %.1f min (%.2f Repos/Minute, %.0f Repos/Stunde)",
        run_duration / 60,
        len(repos) / (run_duration / 60) if run_duration else 0,
        len(repos) / (run_duration / 3600) if run_duration else 0,
    )
    if repo_durations:
        avg = sum(repo_durations) / len(repo_durations)
        logger.info("Durchschnittliche Dauer pro Repo: %.2fs", avg)

    if failed_repos:
        fail_path = FAILED_REPOS_DIR / f"failed_repos_{run_id[:8]}.txt"
        fail_path.write_text("\n".join(failed_repos), encoding="utf-8")
        logger.info("Fehlgeschlagene Repos gespeichert in %s", fail_path)

    logger.info(
        "Rollback bei Bedarf: DELETE FROM repository_contributors WHERE run_id = '%s';", run_id
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
