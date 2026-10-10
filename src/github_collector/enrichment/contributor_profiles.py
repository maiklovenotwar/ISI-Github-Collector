"""Profil-Anreicherung für `contributors` über die GitHub-GraphQL-API.

Holt name/location/company etc. für Contributors, deren Profil noch nie abgefragt
wurde, und vermerkt jeden Abrufversuch in `contributors.enrichment_checked_at`.

Die Tabelle hält eine Zeile pro GitHub-Konto, unabhängig von dessen Rolle. Der
`scope` grenzt ein, welche Zeilen bearbeitet werden:

    new : nur Contributors, die über `repository_contributors` bekannt wurden
          (Mitwirkende an Repositories)
    gap : alle anderen Contributors (in der Regel Repository-Besitzer)
    all : beides zusammen

Eigenschaften, die für lange Läufe über Millionen Zeilen nötig sind:

1. Bulk-DB-Writes (ein Upsert pro Batch statt ein UPDATE pro Login).
2. Sharding über shard_index/shard_count, damit mehrere Prozesse parallel disjunkte
   Teilmengen bearbeiten können (das GraphQL-Rate-Limit gilt pro Token).

Kommandozeile: scripts/enrichment/enrich_contributor_profiles.py
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from dateutil import parser as date_parser
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from github_collector import config
from github_collector.database.models import Contributor
from github_collector.utils.retry import retry_with_backoff

logger = logging.getLogger(__name__)

GRAPHQL_ENDPOINT = f"{config.GITHUB_API['url'].rstrip('/')}/graphql"
BATCH_SIZE = 100
FLUSH_SIZE = 500
SCOPES = ("new", "gap", "all")

# SQLite erlaubt je nach Version nur 999 gebundene Parameter pro Anweisung; bei 16 Spalten
# pro Zeile bleiben 50 Zeilen sicher darunter.
_SQLITE_ROWS_PER_STATEMENT = 50


class GraphQLResponseError(Exception):
    """Die GraphQL-Antwort enthält keinen auswertbaren `data`-Teil."""


def get_candidates(
    engine: Engine, scope: str, shard_index: int = 0, shard_count: int = 1
) -> List[Dict[str, Any]]:
    """Contributors, deren Profil noch nie abgefragt wurde, gefiltert nach scope und Shard.

    Filtert auf enrichment_checked_at IS NULL statt auf leere Profilfelder - viele echte
    GitHub-Profile haben name/location/company alle leer (z.B. 'technoweenie'), die wuerden
    sonst bei jedem erneuten Lauf wieder als "offen" ausgewaehlt und unnoetig erneut
    abgefragt (siehe Fund vom 2026-08-27).
    """
    base = """
        SELECT id, login FROM contributors
        WHERE enrichment_checked_at IS NULL
    """
    if scope == "new":
        base += (
            " AND id IN (SELECT DISTINCT github_user_id FROM repository_contributors "
            "WHERE github_user_id IS NOT NULL)"
        )
    elif scope == "gap":
        base += (
            " AND id NOT IN (SELECT DISTINCT github_user_id FROM repository_contributors "
            "WHERE github_user_id IS NOT NULL)"
        )
    elif scope != "all":
        raise ValueError(f"Unbekannter scope: {scope}")

    if shard_count > 1:
        base += f" AND id % {int(shard_count)} = {int(shard_index)}"

    with engine.connect() as conn:
        rows = conn.execute(text(base)).fetchall()
    return [{"id": r[0], "login": r[1]} for r in rows]


def build_query(logins: List[str]) -> Dict[str, Any]:
    """Baut eine GraphQL-Abfrage, die alle Logins in einem Aufruf holt."""
    parts = []
    variables: Dict[str, str] = {}
    for i, login in enumerate(logins):
        var = f"login{i}"
        parts.append(
            f"""user{i}: user(login: ${var}) {{
  login name email websiteUrl location bio company twitterUsername
  createdAt updatedAt
  repositories {{ totalCount }}
  gists {{ totalCount }}
  followers {{ totalCount }}
  following {{ totalCount }}
}}"""
        )
        variables[var] = login
    decls = ", ".join(f"${k}: String!" for k in variables)
    query = f"query({decls}) {{\n" + "\n".join(parts) + "\n}"
    return {"query": query, "variables": variables}


@retry_with_backoff(
    max_attempts=4, initial_delay=2.0, backoff_factor=2.0, exceptions=requests.RequestException
)
def fetch_batch(
    session: requests.Session, token: str, logins: List[str]
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Fragt die Profile eines Batches ab und gibt (data, errors) der Antwort zurück.

    Raises:
        GraphQLResponseError: Wenn die Antwort keinen `data`-Teil enthält (z.B. bei einem
            Rate-Limit, das GitHub mit HTTP 200 und `data: null` meldet). Ohne diese
            Unterscheidung würde der ganze Batch als "nicht gefunden" vermerkt.
    """
    payload = build_query(logins)
    resp = session.post(
        GRAPHQL_ENDPOINT,
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=config.GITHUB_API["timeout"],
    )
    remaining = int(resp.headers.get("X-RateLimit-Remaining", "999"))
    reset_ts = int(resp.headers.get("X-RateLimit-Reset", "0"))
    if resp.status_code == 403 and remaining == 0:
        wait_sec = max(0, reset_ts - int(time.time()) + 2)
        logger.warning("Rate Limit erreicht, warte %ds...", wait_sec)
        time.sleep(wait_sec)
        resp = session.post(
            GRAPHQL_ENDPOINT,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=config.GITHUB_API["timeout"],
        )
    resp.raise_for_status()
    body = resp.json()
    errors = body.get("errors") or []
    if errors:
        logger.debug("GraphQL Errors (teils erwartet, z.B. geloeschte User): %s", errors)
    data = body.get("data")
    if not isinstance(data, dict):
        raise GraphQLResponseError(f"GraphQL-Antwort ohne Daten: {errors}")
    return data, errors


def _aliases_with_unexpected_errors(errors: List[Dict[str, Any]]) -> set:
    """Aliasse (user0, user1, ...), deren Fehler kein schlichtes "nicht gefunden" ist."""
    aliases = set()
    for error in errors:
        if error.get("type") == "NOT_FOUND":
            continue
        path = error.get("path") or []
        if path:
            aliases.add(path[0])
    return aliases


@retry_with_backoff(
    max_attempts=5, initial_delay=2.0, backoff_factor=2.0, exceptions=OperationalError
)
def mark_checked_only(engine: Engine, ids: List[int]) -> None:
    """Setzt enrichment_checked_at fuer Accounts, die GitHub nicht mehr kennt.

    Bewusst ein reines UPDATE ohne die anderen Spalten anzufassen - siehe
    parse_user_data-Docstring: ein Upsert mit lauter NULLs koennte echte, anderweitig
    gesetzte Daten ueberschreiben.
    """
    if not ids:
        return
    stmt = text("UPDATE contributors SET enrichment_checked_at = :ts WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    with engine.begin() as conn:
        conn.execute(stmt, {"ts": datetime.now(timezone.utc), "ids": ids})


def _upsert_statement(engine: Engine, rows: List[Dict[str, Any]]):
    """Dialektabhängiges Upsert auf den Primärschlüssel `id`."""
    table = Contributor.__table__
    columns = [c for c in rows[0].keys() if c != "id"]
    if engine.dialect.name == "sqlite":
        stmt = sqlite_insert(table).values(rows)
        return stmt.on_conflict_do_update(
            index_elements=["id"], set_={c: stmt.excluded[c] for c in columns}
        )
    stmt = mysql_insert(table).values(rows)
    return stmt.on_duplicate_key_update(**{c: stmt.inserted[c] for c in columns})


@retry_with_backoff(
    max_attempts=5, initial_delay=2.0, backoff_factor=2.0, exceptions=OperationalError
)
def flush_batch(engine: Engine, rows: List[Dict[str, Any]]) -> None:
    """Schreibt die Zeilen in einer Transaktion (alle oder keine)."""
    if not rows:
        return
    step = _SQLITE_ROWS_PER_STATEMENT if engine.dialect.name == "sqlite" else len(rows)
    with engine.begin() as conn:
        for i in range(0, len(rows), step):
            conn.execute(_upsert_statement(engine, rows[i : i + step]))


def flush_rows(engine: Engine, rows: List[Dict[str, Any]]) -> int:
    """Schreibt die Zeilen und gibt die Anzahl der geschriebenen zurück.

    Schlägt der gemeinsame Schreibvorgang fehl, wird Zeile für Zeile geschrieben, damit ein
    einzelner unzulässiger Wert (z.B. ein Login, das durch eine Umbenennung mit einer
    anderen Zeile kollidiert) nicht den ganzen Batch verwirft. Nicht schreibbare Zeilen
    bleiben unverändert und ohne Vermerk; sie werden beim nächsten Lauf erneut ausgewählt.
    """
    if not rows:
        return 0
    try:
        flush_batch(engine, rows)
        return len(rows)
    except Exception as e:
        logger.error("Flush von %d Zeilen fehlgeschlagen, schreibe einzeln: %s", len(rows), e)

    written = 0
    for row in rows:
        try:
            flush_batch(engine, [row])
            written += 1
        except Exception as e:
            logger.error("Contributor %s (ID %s) nicht schreibbar: %s", row["login"], row["id"], e)
    return written


# Spaltenlaengen aus models.py (Contributor) - defensiv einhalten, da GitHub-Freitextfelder
# (v.a. twitterUsername) gelegentlich laengere/kaputte Werte liefern als das Schema erwartet
# (siehe Absturz 2026-08-10: "Data too long for column 'twitter_username'" hat den ganzen
# mehrstuendigen Lauf gekillt, weil der Fehler ungefangen bis main() durchschlug).
_MAX_LEN = {
    "login": 255,
    "name": 255,
    "email": 255,
    "blog": 255,
    "location": 255,
    "company": 255,
    "twitter_username": 50,
}


def _truncate(value: Optional[str], field: str) -> Optional[str]:
    if value is None:
        return None
    max_len = _MAX_LEN.get(field)
    if max_len and len(value) > max_len:
        logger.warning("Feld %s zu lang (%d Zeichen), kuerze auf %d.", field, len(value), max_len)
        return value[:max_len]
    return value


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return date_parser.parse(value)
    except Exception:
        return None


def parse_user_data(
    user_data: Optional[Dict[str, Any]], candidate: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Baut die zu schreibende Zeile fuer einen Treffer, sonst None.

    Der "nicht gefunden"-Fall (geloeschter/umbenannter Account) wird bewusst NICHT hier
    behandelt, sondern separat per reinem enrichment_checked_at-UPDATE (siehe
    mark_checked_only) - ein upsert mit lauter NULL-Feldern wuerde bei einem Contributor,
    der anderweitig schon echte Daten hat, diese ueberschreiben.
    """
    if not user_data:
        return None

    row = {
        "id": candidate["id"],
        "login": user_data.get("login") or candidate["login"],
        "name": user_data.get("name"),
        "email": user_data.get("email"),
        "blog": user_data.get("websiteUrl"),
        "location": user_data.get("location"),
        "bio": user_data.get("bio"),
        "company": user_data.get("company"),
        "twitter_username": user_data.get("twitterUsername"),
        "created_at": _parse_datetime(user_data.get("createdAt")),
        "updated_at": _parse_datetime(user_data.get("updatedAt")),
        "public_repos": (user_data.get("repositories") or {}).get("totalCount"),
        "public_gists": (user_data.get("gists") or {}).get("totalCount"),
        "followers": (user_data.get("followers") or {}).get("totalCount"),
        "following": (user_data.get("following") or {}).get("totalCount"),
        "enrichment_checked_at": datetime.now(timezone.utc),
    }
    for field in _MAX_LEN:
        row[field] = _truncate(row.get(field), field)
    return row


def process_batch(
    session: requests.Session,
    token: str,
    engine: Engine,
    batch: List[Dict[str, Any]],
    buffer: List[Dict[str, Any]],
    dry_run: bool,
) -> Tuple[int, int]:
    """Fragt einen Batch ab, haengt Ergebnisse an buffer an. Gibt (not_found, parsed) zurueck."""
    logins = [c["login"] for c in batch]
    try:
        data, errors = fetch_batch(session, token, logins)
    except Exception as e:
        logger.error("Batch fehlgeschlagen, ueberspringe: %s", e)
        return 0, 0

    # Als "nicht gefunden" gilt nur, was GitHub ausdrücklich mit null beantwortet hat.
    # Fehlt der Alias in der Antwort oder trägt er einen anderen Fehler als NOT_FOUND,
    # bleibt die Zeile ohne Vermerk und wird beim nächsten Lauf erneut ausgewählt.
    unexpected = _aliases_with_unexpected_errors(errors)
    not_found_ids = []
    parsed = 0
    for i, c in enumerate(batch):
        alias = f"user{i}"
        if alias not in data or (data[alias] is None and alias in unexpected):
            logger.warning("Keine verwertbare Antwort fuer %s, kein Vermerk gesetzt.", c["login"])
            continue
        row = parse_user_data(data[alias], c)
        if row is None:
            not_found_ids.append(c["id"])
        else:
            buffer.append(row)
            parsed += 1

    if not_found_ids and not dry_run:
        try:
            mark_checked_only(engine, not_found_ids)
        except Exception as e:
            logger.error(
                "Markieren von %d nicht gefundenen IDs fehlgeschlagen: %s", len(not_found_ids), e
            )
    not_found = len(not_found_ids)

    if len(buffer) >= FLUSH_SIZE:
        if not dry_run:
            flush_rows(engine, buffer)
        buffer.clear()
    return not_found, parsed
