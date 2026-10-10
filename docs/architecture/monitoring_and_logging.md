# Monitoring & Logging

## 1. Logging Configuration

Logging is centrally configured via `LOG_CONFIG` in
`src/github_collector/config.py`. Each operation writes to its own log file
under `logs/`:

| Operation | Log file |
|-----------|----------|
| Repository collection | `logs/collection/repository_collection.log` |
| Export | `logs/export/export.log` |
| Geocoding | `logs/geocoding/geocoding.log` |
| Reset | `logs/maintenance/reset_and_collect.log` |
| Orphaned-owner cleanup | `logs/maintenance/cleanup_orphaned_owners.log` |

Log level, format, rotation size, and backup count are configurable via
`LOG_LEVEL`, `LOG_FORMAT`, `LOG_DATE_FORMAT`, `LOG_MAX_SIZE`, and
`LOG_BACKUP_COUNT`.

## 2. Performance Tracking

Independent of the log files above, `utils/performance_tracker.py`
(`PerformanceTracker`) records per-run timing and API metrics — batch
timings, API call counts, cache hits, token usage and rotations — and
writes them as JSON under `logs/performance/`. Controlled by
`ENABLE_PERFORMANCE_TRACKING` (default: on).

## 3. Batch Logging & Trace IDs

Repository collection processes data in batches. Each batch is assigned a
6-character trace ID (`batch_id = uuid.uuid4().hex[:6]`, e.g. `ab12ef`) at
the start of `_process_repositories_batch()`. Every log line emitted while
processing that batch — owner extraction, organization/contributor
persistence, bulk upserts, final metrics — is tagged with the same
`batch_id`, for example:

```
INFO - [ab12ef] Starte Verarbeitung von Batch mit 100 Roh-Repositories
INFO - [ab12ef] Dual Extraction: 42 Owners (Base, inkl. Org-Owner), 8 Organizations
INFO - [ab12ef] Batch completed: 97 processed (raw=100, time=4.32s, avg=0.04s/repo, loss=3.0%)
```

This makes it possible to reconstruct everything that happened for one
batch by filtering the log on its trace ID, and to verify in tests that
batch-start, owner/organization, and batch-completion log lines all
correspond to the same batch.

## 4. Prometheus Metrics

`src/github_collector/monitoring/metrics.py` defines Prometheus metrics and
an HTTP endpoint; `start_metrics_server(port)` exposes them at
`http://localhost:{port}/metrics`.

| Metric | Type | Labels | Description |
|--------|------|--------|--------------|
| `github_collector_repositories_total` | Counter | `status` (valid/invalid/skipped) | Repositories processed |
| `github_collector_api_requests_total` | Counter | `method`, `endpoint`, `status_code` | GitHub API requests |
| `github_collector_db_operations_total` | Counter | `table`, `operation` | Database operations |
| `github_collector_rate_limit_remaining` | Gauge | `token_index` | Remaining quota per token |
| `github_collector_active_threads` | Gauge | — | Active worker threads |
| `github_collector_batch_size_current` | Gauge | — | Current adaptive batch size |
| `github_collector_batch_processing_seconds` | Histogram | — | Batch processing duration |

Enable via the CLI:

```bash
python scripts/collection/collect_repositories.py \
  --enable-metrics \
  --metrics-port 8000 \
  --non-interactive --time-range week --limit 50 --min-stars 100
```

### Grafana Dashboard

A ready-made dashboard definition is provided at
`monitoring/grafana_dashboard.json`, grouped into four sections:

- **Health & KPIs**: total valid repositories collected, throughput
  (repos/min), active threads, current batch size
- **Token Health**: rate-limit remaining per token
- **Performance**: batch-processing time distribution, average and p95
- **Data Quality**: valid-vs-invalid processing rate, invalid-repository
  percentage
