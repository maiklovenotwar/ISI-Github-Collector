# Test Infrastructure

## Overview

The test suite follows a layered strategy:

| Layer | Focus | Speed | Isolation | Location |
|-------|-------|-------|-----------|----------|
| Unit | Individual functions/classes | fast | fully mocked | `tests/unit/` |
| Integration | Interaction between components | medium | partially mocked | `tests/integration/` |
| Smoke | Critical end-to-end paths | slow | real API/DB | `tests/smoke/` |

Manual diagnostic/EXPLAIN-PLAN scripts against a live Oracle/Orbis connection
are not part of the test suite; they live under `scripts/diagnostics/`.

## 1. Unit Tests (`tests/unit/`)

| Test file | Focus | Tests |
|-----------|-------|-------|
| `test_config.py` | Configuration loading | 5 |
| `test_logging_config.py` | Logging setup | 5 |
| `test_secrets.py` | `SecretManager` (tokens) | 7 |
| `test_schemas.py` | Pydantic validation | 10 |
| `test_repository_collector_validation.py` | Validation firewall | 4 |
| `test_repository_collector_logic.py` | Collector logic (periods, batches) | 4 |
| `test_owner_processor_logic.py` | Owner deduplication | 4 |
| `test_utils_logic.py` | `retry_with_backoff`, `AdaptiveBatchManager` | 5 |
| `test_token_pool_concurrency.py` | Thread-safe token rotation | 5 |
| `test_performance.py` | Performance utilities | 5 |

```bash
# All unit tests
pytest tests/unit/ -v

# A single test file
pytest tests/unit/test_schemas.py -v

# With coverage
pytest tests/unit/ --cov=src/github_collector --cov-report=term-missing
```

### Mocking Patterns

API mocking:
```python
from unittest.mock import MagicMock, patch

@patch("github_collector.api.github_api.GitHubAPI.search_repositories")
def test_example(mock_search):
    mock_search.return_value = {"total_count": 100, "items": [...]}
    # ...
```

Database mocking:
```python
mock_db = MagicMock()
collector = RepositoryCollector(github_client=mock_api, db=mock_db)
```

## 2. Integration Tests (`tests/integration/`)

| Test file | Focus | Real resources |
|-----------|-------|-----------------|
| `test_db_persistence.py` | Pipeline → SQLite (persistence proof) | SQLite file |
| `test_collect_cli_integration.py` | CLI run (`--analyze`, `--non-interactive`) | SQLite (`:memory:`) |
| `test_api_retry.py` | Retry mechanism | mocked |
| `test_retry_integration.py` | Retry + backoff | mocked |
| `test_token_rotation_live.py` | Multi-token rotation | real GitHub API (`@pytest.mark.external`) |
| `test_orbis_connection.py` | Live Oracle/Orbis connectivity | real Orbis connection (`@pytest.mark.orbis_integration`) |

### Persistence Proof (`test_db_persistence.py`)

Purpose: a stable proof that the pipeline persists data correctly.

```
Mock API → RepositoryCollector → Pydantic → OwnerProcessor → DB upsert → SQLite
```

Not tested through the CLI because `session_scope()` (called with no
argument) uses the global `SessionLocal`, and CLI argument parsing plus
singleton side effects make isolation harder; a direct component test gives
a more stable, reproducible proof.

```bash
pytest tests/integration/test_db_persistence.py -v
```

### CLI Integration (`test_collect_cli_integration.py`)

Covers `--analyze` mode and `--non-interactive` mode (both in-memory and
file-based SQLite). Hard database assertions (row counts) live in
`test_db_persistence.py` instead.

### Live Token Rotation (`test_token_rotation_live.py`)

Requires two GitHub tokens from **different** GitHub accounts in `.env`
(`GITHUB_API_TOKENS=token1,token2`). Marked `@pytest.mark.external` and
excluded from the default run (see "Pytest Markers" below); covers
multi-token initialization, rate-limit queries, concurrent API calls, and
token distribution.

```bash
pytest tests/integration/test_token_rotation_live.py -v -m external
```

### Live Orbis Connection (`test_orbis_connection.py`)

Requires VPN access to the Orbis Oracle host and valid `ORBIS_*`
credentials in `.env`. Marked `@pytest.mark.orbis_integration` and excluded
from the default run.

```bash
pytest tests/integration/test_orbis_connection.py -v -m orbis_integration
```

## 3. Smoke Tests (`tests/smoke/`)

| Test file | Focus | Requires |
|-----------|-------|----------|
| `test_github_api_smoke.py` | GitHub API reachable | a real token |
| `test_repository_collector_smoke.py` | Collector basic functionality | a real token + DB |

Both tests issue requests to the GitHub API. They carry the markers `smoke` and `external`
and are therefore deselected in a plain `pytest` run; they are run explicitly:

```bash
pytest tests/smoke/ -v -m smoke
```

## 4. Pytest Markers

Defined in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
addopts = "-m 'not orbis_integration and not external'"
markers = [
    "smoke: short-running end-to-end/integration scenarios",
    "external: tests requiring external services (e.g. the GitHub API)",
    "slow: long-running tests (e.g. rate-limit tests with real API calls)",
    "integration: broader integration tests (e.g. CLI + DB with a mocked API)",
    "orbis_integration: tests requiring a live Oracle/Orbis connection (VPN needed)",
]
```

`external` and `orbis_integration` tests are excluded by default — both
require live, real-world connections (a second GitHub account's token, or
VPN+Orbis access) that most contributors and CI runs will not have.

```bash
# Default run (external + orbis_integration excluded automatically)
pytest tests/

# Explicitly include external tests
pytest tests/ -m "external"

# Only integration tests
pytest tests/integration/ -m integration
```

## 5. Test Fixtures (`tests/conftest.py`)

| Fixture | Scope | Description |
|---------|-------|--------------|
| `temp_db_path` | function | Path to a temporary SQLite file |
| `test_db` | function | `GitHubDatabase` instance backed by `temp_db_path` |
| `mock_github_api` | function | Fully mocked `GitHubAPI` |

```python
def test_something(mock_github_api, test_db):
    collector = RepositoryCollector(github_client=mock_github_api, db=test_db)
    collector._process_repositories_batch([...])
```

## 6. Patching the Global `SessionLocal`

`session_scope()`, called with no argument, uses the global `SessionLocal`.
To redirect it to a test database:

```python
import github_collector.database.database as db_module
from github_collector.database.database import _create_engine

test_engine = _create_engine(f"sqlite:///{tmp_path}/test.db")
db_module._engine = test_engine
db_module.SessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)

db = GitHubDatabase(db_url)
```

## 7. Running Tests Locally and in CI

```bash
# Fast feedback loop (unit tests only)
pytest tests/unit/ -x -v

# Before committing (default marker filtering)
pytest tests/ -v

# Full run including external/live tests (requires tokens + VPN as applicable)
pytest tests/ -v -m "external or orbis_integration or not (external or orbis_integration)"
```

Suggested CI stages: lint → unit tests → integration tests (`-m
"not external"`) → smoke tests (scheduled, with CI-provisioned tokens, not
on every commit).

## 8. Test Coverage (measured 2026-10-07; `enrichment/cctld.py` and overall figure 2026-10-09)

| Module | Coverage |
|--------|----------|
| `schemas/` | 89-96% per file |
| `repository_collector.py` | 60% |
| `database/database.py` | 47% |
| `api/github_api.py` | 43% |
| `enrichment/orbis_matching_logic.py` | 94% |
| `enrichment/hq_detection.py` | 81% |
| `enrichment/orbis_client.py` | 73% |
| `enrichment/cctld.py` | 99% |
| `enrichment/graphql_handler.py`, `enrichment/updater.py`, `export/csv_export.py` | 0% (no unit tests yet) |
| **Overall** | 50% |

```bash
pytest tests/ --cov=src/github_collector --cov-report=term-missing
```

## 9. Known Architectural Quirks

### Global `SessionLocal`

`_process_repositories_batch` calls `session_scope()` with no argument,
which uses the global `SessionLocal`. CLI integration tests therefore
cannot assert on DB persistence directly; `test_db_persistence.py` patches
`SessionLocal` to work around this (see Section 6).

### Token Rotation Is Not Deterministic

`GitHubTokenPool.get_best_token()` selects based on `remaining` and
`last_used`; when these are tied, selection order is not guaranteed. Tests
assert on distribution (e.g. "both tokens were used"), not an exact
sequence.

## 10. Checklist for New Tests

- [ ] Appropriate pytest marker set (`@pytest.mark.integration`, etc.)
- [ ] Fixtures from `conftest.py` reused where applicable
- [ ] External resources (API, DB) mocked unless the test is explicitly an
      integration/smoke/external test
- [ ] Temporary files via the `tmp_path` fixture
- [ ] Assertions are specific, not just "no exception was raised"
- [ ] Docstring explains the scenario and expected outcome

## 11. Monitoring & Prometheus Metrics

See `docs/architecture/monitoring_and_logging.md` for the monitoring
architecture (metrics, types, dashboard). Relevant for testing:

- Metrics can be enabled via `--enable-metrics` / `--metrics-port`.
- When enabled, an HTTP endpoint (default `http://localhost:8000/metrics`)
  is available and can be inspected from integration/smoke tests.

```bash
python scripts/collection/collect_repositories.py \
  --enable-metrics \
  --metrics-port 8000 \
  --non-interactive --time-range week --limit 50 --min-stars 100
```

## 12. Batch Logging & Trace IDs

See `docs/architecture/monitoring_and_logging.md`, Section 3, for how
`batch_id` trace IDs work. Relevant for testing: integration/smoke tests
can assert that batch-start and batch-completion log lines, and the
owner/organization and bulk-upsert log lines in between, all carry the
same `batch_id`.
