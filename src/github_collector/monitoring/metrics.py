"""Prometheus-Metriken für den GitHub Data Collector.

Phase 4.1: Definiert alle Metriken und stellt einen HTTP-Server bereit.

Verwendung:
    from github_collector.monitoring import (
        start_metrics_server,
        record_repository_processed,
        record_api_request,
    )

    # Server auf Port 8000 starten
    start_metrics_server(8000)

    # Metriken aufzeichnen
    record_repository_processed(status="valid")
    record_api_request(method="GET", endpoint="/repos", status_code=200)
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Generator

from prometheus_client import Counter, Gauge, Histogram, start_http_server

logger = logging.getLogger(__name__)

# =============================================================================
# COUNTER METRIKEN
# =============================================================================

collected_repositories_total = Counter(
    name="github_collector_repositories_total",
    documentation="Anzahl verarbeiteter Repositories",
    labelnames=["status"],  # valid, invalid, skipped
)

github_api_requests_total = Counter(
    name="github_collector_api_requests_total",
    documentation="Anzahl GitHub API Requests",
    labelnames=["method", "endpoint", "status_code"],
)

db_operations_total = Counter(
    name="github_collector_db_operations_total",
    documentation="Anzahl Datenbank-Operationen",
    labelnames=["table", "operation"],  # table: repositories/contributors/organizations
    # operation: insert/update/upsert/delete
)

# =============================================================================
# GAUGE METRIKEN
# =============================================================================

rate_limit_remaining = Gauge(
    name="github_collector_rate_limit_remaining",
    documentation="Verbleibende GitHub API Calls bis zum Rate-Limit-Reset",
    labelnames=["token_index"],  # Token-Index für Multi-Token-Setups
)

active_threads = Gauge(
    name="github_collector_active_threads",
    documentation="Anzahl aktiver Worker-Threads (Phase 2.3 Parallelisierung)",
)

batch_size_current = Gauge(
    name="github_collector_batch_size_current",
    documentation="Aktuelle Batch-Größe des AdaptiveBatchManager",
)

# =============================================================================
# HISTOGRAM METRIKEN
# =============================================================================

batch_processing_seconds = Histogram(
    name="github_collector_batch_processing_seconds",
    documentation="Dauer der Batch-Verarbeitung in Sekunden",
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

# =============================================================================
# SERVER
# =============================================================================

_server_started = False


def start_metrics_server(port: int = 8000) -> bool:
    """Startet den Prometheus HTTP Metrics Server.

    Der Server stellt Metriken unter http://localhost:{port}/metrics bereit.

    Args:
        port: HTTP-Port für den Metrics-Endpoint (default: 8000)

    Returns:
        True wenn Server gestartet wurde, False wenn bereits aktiv
    """
    global _server_started

    if _server_started:
        logger.warning(f"Metrics server already running, skipping start on port {port}")
        return False

    try:
        start_http_server(port)
        _server_started = True
        logger.info(f"Prometheus metrics server started on http://localhost:{port}/metrics")
        return True
    except OSError as e:
        logger.error(f"Failed to start metrics server on port {port}: {e}")
        return False


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def record_repository_processed(status: str = "valid") -> None:
    """Zeichnet ein verarbeitetes Repository auf.

    Args:
        status: "valid", "invalid", oder "skipped"
    """
    collected_repositories_total.labels(status=status).inc()


def record_api_request(
    method: str,
    endpoint: str,
    status_code: int,
) -> None:
    """Zeichnet einen GitHub API Request auf.

    Args:
        method: HTTP-Methode (GET, POST, etc.)
        endpoint: API-Endpoint (z.B. "/search/repositories", "/repos")
        status_code: HTTP Status-Code (200, 403, 404, etc.)
    """
    # Normalisiere Endpoint (entferne Parameter-Details)
    normalized_endpoint = _normalize_endpoint(endpoint)
    github_api_requests_total.labels(
        method=method.upper(),
        endpoint=normalized_endpoint,
        status_code=str(status_code),
    ).inc()


def record_db_operation(
    table: str,
    operation: str,
    count: int = 1,
) -> None:
    """Zeichnet Datenbank-Operationen auf.

    Args:
        table: Tabellenname (repositories, contributors, organizations)
        operation: Operationstyp (insert, update, upsert, delete)
        count: Anzahl betroffener Zeilen (default: 1)
    """
    db_operations_total.labels(
        table=table.lower(),
        operation=operation.lower(),
    ).inc(count)


@contextmanager
def record_batch_processing() -> Generator[None, None, None]:
    """Context-Manager zum Messen der Batch-Verarbeitungszeit.

    Verwendung:
        with record_batch_processing():
            process_batch(repos)

    Yields:
        None
    """
    start_time = time.perf_counter()
    try:
        yield
    finally:
        duration = time.perf_counter() - start_time
        batch_processing_seconds.observe(duration)


def update_rate_limit(token_index: int, remaining: int) -> None:
    """Aktualisiert die Rate-Limit-Gauge für ein Token.

    Args:
        token_index: Index des Tokens im Pool (0-basiert)
        remaining: Verbleibende API-Calls
    """
    rate_limit_remaining.labels(token_index=str(token_index)).set(remaining)


def update_active_threads(count: int) -> None:
    """Aktualisiert die Anzahl aktiver Worker-Threads.

    Args:
        count: Anzahl aktiver Threads
    """
    active_threads.set(count)


def update_batch_size(size: int) -> None:
    """Aktualisiert die aktuelle Batch-Größe.

    Args:
        size: Aktuelle Batch-Größe
    """
    batch_size_current.set(size)


# =============================================================================
# INTERNAL HELPERS
# =============================================================================


def _normalize_endpoint(endpoint: str) -> str:
    """Normalisiert API-Endpoints für konsistente Labels.

    Entfernt dynamische Pfad-Segmente wie Owner/Repo-Namen.

    Args:
        endpoint: Roher API-Endpoint

    Returns:
        Normalisierter Endpoint

    Examples:
        "/repos/user/repo" → "/repos/{owner}/{repo}"
        "/users/testuser" → "/users/{user}"
        "/search/repositories" → "/search/repositories"
    """
    if not endpoint:
        return "unknown"

    # Entferne Query-Parameter
    endpoint = endpoint.split("?")[0]

    # Normalisiere bekannte Patterns
    parts = endpoint.strip("/").split("/")

    if len(parts) >= 3 and parts[0] == "repos":
        # /repos/{owner}/{repo}[/...]
        normalized = "/repos/{owner}/{repo}"
        if len(parts) > 3:
            normalized += "/" + "/".join(parts[3:])
        return normalized

    if len(parts) >= 2 and parts[0] == "users":
        # /users/{user}[/...]
        normalized = "/users/{user}"
        if len(parts) > 2:
            normalized += "/" + "/".join(parts[2:])
        return normalized

    if len(parts) >= 2 and parts[0] == "orgs":
        # /orgs/{org}[/...]
        normalized = "/orgs/{org}"
        if len(parts) > 2:
            normalized += "/" + "/".join(parts[2:])
        return normalized

    # Keine Normalisierung nötig
    return "/" + "/".join(parts) if parts else "/"
