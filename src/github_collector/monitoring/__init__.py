"""Monitoring & Observability für den GitHub Data Collector.

Phase 4.1: Prometheus-Metriken für:
- Repository-Verarbeitung (Counts, Validierung)
- GitHub API (Requests, Rate-Limits)
- Datenbank-Operationen
- Performance (Batch-Zeiten, Thread-Auslastung)
"""

from .metrics import (
    active_threads,
    # Histogram
    batch_processing_seconds,
    batch_size_current,
    # Counter
    collected_repositories_total,
    db_operations_total,
    github_api_requests_total,
    # Gauge
    rate_limit_remaining,
    record_api_request,
    record_batch_processing,
    record_db_operation,
    # Helper - Recording
    record_repository_processed,
    # Server
    start_metrics_server,
    update_active_threads,
    update_batch_size,
    # Helper - Updates
    update_rate_limit,
)

__all__ = [
    # Counter
    "collected_repositories_total",
    "github_api_requests_total",
    "db_operations_total",
    # Gauge
    "rate_limit_remaining",
    "active_threads",
    "batch_size_current",
    # Histogram
    "batch_processing_seconds",
    # Server
    "start_metrics_server",
    # Helper - Recording
    "record_repository_processed",
    "record_api_request",
    "record_db_operation",
    "record_batch_processing",
    # Helper - Updates
    "update_rate_limit",
    "update_active_threads",
    "update_batch_size",
]
