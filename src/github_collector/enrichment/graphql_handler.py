"""
GraphQL Handler für Repository Stats Enrichment via GitHub GraphQL API
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional

from gql import Client
from gql.transport.requests import RequestsHTTPTransport

from github_collector.config import BATCH_CONFIG, GITHUB_API
from github_collector.utils.secrets import SecretManager


class GraphQLHandler:
    """
    Handler für die Kommunikation mit der GitHub GraphQL API zur Anreicherung
    von Repository-Statistiken.

    Initialisiert einen GraphQL-Client mit Token und Endpunkt, unterstützt
    Batching und Fehlerbehandlung.

    :param github_token: GitHub API Token (optional, sonst via Umgebungsvariable)
    :param batch_size: Anzahl der Repositories pro API-Batch
    """

    def __init__(self, github_token: Optional[str] = None, batch_size: Optional[int] = None):
        # Bevorzugt explizit übergebenes Token, sonst zentralen SecretManager verwenden
        if github_token:
            self.github_token = github_token
        else:
            try:
                tokens = SecretManager.get_github_tokens()
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
            if not tokens:
                raise ValueError("Kein GitHub API-Token verfügbar.")
            self.github_token = tokens[0]
        if batch_size is None:
            batch_size = BATCH_CONFIG["graphql_limit"]
        self.batch_size = batch_size
        self.endpoint = f"{GITHUB_API['url'].rstrip('/')}/graphql"
        self.client = Client(
            transport=RequestsHTTPTransport(
                url=self.endpoint,
                headers={"Authorization": f"Bearer {self.github_token}"},
                retries=GITHUB_API["retry_count"],
                timeout=GITHUB_API["timeout"],  # Timeout in Sekunden
            ),
            fetch_schema_from_transport=False,
        )

    def fetch_repo_stats(  # noqa: C901
        self, repos: List[Dict[str, str]]
    ) -> (List[Dict[str, Any]], List[List[Dict[str, str]]]):
        """
        Fragt für eine Liste von Repositories (owner/name) die gewünschten
        Statistiken per GitHub GraphQL API ab.

        Führt die Abfrage in Batches durch, behandelt Rate-Limits, implementiert
        Retry-Logik (3 Versuche) mit exponentiellem Backoff und speichert den
        Fortschritt in einer Checkpoint-Datei.

        :param repos: Liste von Dictionaries mit owner und name der Repositories
        :return: Tuple (Liste mit Ergebnis-Dicts, Liste der fehlgeschlagenen
            Batches)

        Jedes Ergebnis-Dict enthält: ``repo_id``, ``calculated_pr_count``,
        ``calculated_commit_count``, ``calculated_contributor_count``. Bei
        Fehlern werden die betroffenen Batches gesammelt und zurückgegeben.
        Fortschritt kann über die Checkpoint-Datei wieder aufgenommen werden.
        """
        import json

        import requests

        logger = logging.getLogger(__name__)
        results = []
        failed_batches = []
        checkpoint_path = "enrich_checkpoint.txt"
        # Checkpoint laden
        start_batch_idx = 0
        if os.path.exists(checkpoint_path):
            try:
                with open(checkpoint_path, "r") as f:
                    start_batch_idx = int(f.read().strip())
                logger.info(f"Checkpoint gefunden: Starte ab Batch-Index {start_batch_idx}.")
            except Exception as e:
                logger.warning(
                    f"Checkpoint-Datei konnte nicht gelesen werden: {e}. Ignoriere Checkpoint."
                )
                start_batch_idx = 0
        all_batches = list(self._batch(repos, self.batch_size))
        for batch_idx, batch in enumerate(all_batches):
            if batch_idx < start_batch_idx:
                continue  # Überspringe bereits verarbeitete Batches
            query, variables = self._build_batch_query(batch)
            success = False
            max_attempts = GITHUB_API["retry_count"]
            for attempt in range(max_attempts):  # Konfigurierbare Anzahl Versuche pro Batch
                try:
                    logger.debug(
                        "Starte GraphQL-Request für Batch %d/%d mit %d Repos " "(Versuch %d)",
                        batch_idx + 1,
                        len(all_batches),
                        len(batch),
                        attempt + 1,
                    )
                    # --- Direkter POST-Request für RateLimit-Header ---
                    session = requests.Session()
                    headers = {
                        "Authorization": f"Bearer {self.github_token}",
                        "Content-Type": "application/json",
                    }
                    gql_payload = {"query": query, "variables": variables}
                    resp = session.post(
                        self.endpoint,
                        headers=headers,
                        data=json.dumps(gql_payload),
                        timeout=GITHUB_API["timeout"],
                    )
                    # Rate Limit Handling
                    remaining = int(resp.headers.get("X-RateLimit-Remaining", "999"))
                    reset_ts = int(resp.headers.get("X-RateLimit-Reset", "0"))
                    logger.info(
                        "GitHub RateLimit: remaining=%d, reset=%d",
                        remaining,
                        reset_ts,
                    )
                    if remaining <= 3:
                        wait_sec = max(0, reset_ts - int(time.time()) + 2)
                        logger.warning(
                            "Rate Limit fast erreicht! Schlafe %d Sekunden " "bis zum Reset...",
                            wait_sec,
                        )
                        time.sleep(wait_sec)
                    # Fehlerbehandlung HTTP
                    if resp.status_code >= 500:
                        logger.error(
                            "HTTP 5xx Fehler: %s %s. Versuch %d",
                            resp.status_code,
                            resp.reason,
                            attempt + 1,
                        )
                        raise requests.exceptions.HTTPError(
                            f"HTTP 5xx Fehler: {resp.status_code} {resp.reason}"
                        )
                    if resp.status_code == 403 and remaining == 0:
                        logger.warning("Rate Limit erreicht (403)! Schlafe bis zum Reset...")
                        wait_sec = max(0, reset_ts - int(time.time()) + 2)
                        time.sleep(wait_sec)
                        continue
                    resp.raise_for_status()
                    # --- Robust: Prüfe, ob data-Teil vorhanden ist ---
                    response_json = resp.json()
                    response_data = response_json.get("data")
                    if response_data is None:
                        logger.error(
                            "GraphQL-Antwort ohne 'data'-Feld: %s. Batch wird erneut versucht.",
                            response_json,
                        )
                        raise Exception("Keine 'data'-Sektion in der GraphQL-Antwort.")
                    logger.debug("GraphQL-Rohantwort: %s", response_data)
                    current_batch_results = []
                    failed_repos_in_batch = []
                    for idx, repo in enumerate(batch):
                        repo_key = f"repo{idx}"
                        repo_data = response_data.get(repo_key)
                        if not isinstance(repo_data, dict) or repo_data is None:
                            failed_repos_in_batch.append(repo)
                            logger.warning(
                                "Keine Daten für Repo %s/%s gefunden (repo_data ist %s: %s)",
                                repo["owner"],
                                repo["name"],
                                type(repo_data),
                                repo_data,
                            )
                            continue
                        if repo_data.get("databaseId") is None:
                            failed_repos_in_batch.append(repo)
                            logger.warning(
                                "Keine databaseId für Repo %s/%s gefunden: %s",
                                repo["owner"],
                                repo["name"],
                                repo_data,
                            )
                            continue
                        default_branch = repo_data.get("defaultBranchRef")
                        if not isinstance(default_branch, dict):
                            default_branch = {}
                        target = default_branch.get("target")
                        if not isinstance(target, dict):
                            target = {}
                        history = target.get("history", {})
                        if not isinstance(history, dict):
                            history = {}
                        current_batch_results.append(
                            {
                                "repo_id": repo_data["id"],
                                "database_id": repo_data.get("databaseId"),
                                "calculated_pr_count": repo_data.get("pullRequests", {}).get(
                                    "totalCount", 0
                                ),
                                "calculated_commit_count": history.get("totalCount", 0),
                                "calculated_contributor_count": 0,
                            }
                        )
                    if failed_repos_in_batch:
                        failed_batches.append(failed_repos_in_batch)
                    results.extend(current_batch_results)
                    logger.debug(f"Extrahierte results für Batch: {current_batch_results}")
                    success = True
                    break
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                    logger.error(
                        "Netzwerkfehler/Timeout bei Batch %d (Versuch %d): %s. Batch: %s",
                        batch_idx + 1,
                        attempt + 1,
                        e,
                        [r["owner"] + "/" + r["name"] for r in batch],
                    )
                    time.sleep(2**attempt)  # Exponentielles Backoff
                except requests.exceptions.HTTPError as e:
                    logger.error(f"HTTPError bei Batch {batch_idx+1} (Versuch {attempt+1}): {e}")
                    time.sleep(2**attempt)
                except Exception as e:
                    batch_repr = [
                        r.get("owner", "N/A") + "/" + r.get("name", "N/A")
                        for r in batch
                        if isinstance(r, dict)
                    ]
                    logger.error(
                        "Allgemeiner Fehler bei Batch %d (Versuch %d): %s. Batch: %s",
                        batch_idx + 1,
                        attempt + 1,
                        e,
                        batch_repr,
                        exc_info=True,
                    )
                    time.sleep(2**attempt)
            # Checkpoint nach jedem Batch schreiben
            with open(checkpoint_path, "w") as f:
                f.write(str(batch_idx + 1))
            if not success:
                logger.error(
                    "Batch endgültig fehlgeschlagen: "
                    f"{[r['owner'] + '/' + r['name'] for r in batch]}"
                )
                failed_batches.append(batch)
        # Checkpoint entfernen, wenn alles erfolgreich
        if os.path.exists(checkpoint_path):
            os.remove(checkpoint_path)
        logger.debug(f"Alle extrahierten results (gesamt): {results}")
        return results, failed_batches

    def _batch(self, iterable, n):
        length = len(iterable)
        for ndx in range(0, length, n):
            yield iterable[ndx : min(ndx + n, length)]

    def _build_batch_query(self, batch):
        query_parts = []
        variables = {}
        history_limit = BATCH_CONFIG["graphql_limit"]
        for idx, repo in enumerate(batch):
            owner = repo["owner"]
            name = repo["name"]
            var_owner = f"owner{idx}"
            var_name = f"name{idx}"
            query_parts.append(
                f"""repo{idx}: repository(owner: ${var_owner}, name: ${var_name}) {{
  id
  databaseId
  pullRequests {{ totalCount }}
  defaultBranchRef {{
    target {{ ... on Commit {{
      history(first: {history_limit}) {{
        totalCount
      }}
    }} }}
  }}
}}"""
            )
            variables[var_owner] = owner
            variables[var_name] = name
        variable_decls = ", ".join([f"${k}: String!" for k in variables.keys()])
        query_body = chr(10).join(query_parts)
        query = f"query({variable_decls}) {{\n{query_body}\n}}"
        return query, variables
