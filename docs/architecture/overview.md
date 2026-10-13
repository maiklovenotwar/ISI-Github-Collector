# Architecture Overview

## Introduction

The GitHub Data Collector is a Python application for collecting metadata on
GitHub repositories, contributors, and organizations. It is organized into
modules with a clear separation of concerns, each responsible for one aspect
of collection, enrichment, storage, or export.

## Design Principles

1. **Modularity**: the application is divided into modules with a single,
   well-defined responsibility.
2. **Separation of concerns**: each module owns one aspect of the
   application; cross-cutting logic (configuration, logging) is centralized.
3. **Configurability**: behavior is controlled through a central
   configuration module and environment variables, not hardcoded values.
4. **Testability**: components are designed to be testable in isolation
   (e.g. via dependency injection of the database/API client).
5. **Extensibility**: new data sources, export formats, or CLI commands can
   be added without modifying existing components.

## Main Components

### 1. Configuration (`config.py`)

Central configuration module: paths, database settings, API configuration,
and logging configuration.

### 2. API Client & GraphQL Handler (`api/github_api.py`, `enrichment/graphql_handler.py`)

The API client handles communication with the GitHub REST API. Repository
statistics enrichment additionally uses a dedicated GraphQL handler.

- Token-pool management for multiple API tokens
- Rate-limit handling
- Caching of API responses
- GraphQL batch enrichment: a single failed item in a batch is logged
  individually and does not abort the rest of the batch
- Error causes are logged with a full stack trace for traceability
- Automatic retries on transient API errors (e.g. HTTP 5xx)

### 3. Database (`database/`)

Storage and access for collected data:

- Database models (`models.py`)
- Database access layer (`database.py`)
- Schema migrations via versioned SQL scripts in `scripts/migrations/`

### 4. Repository Collector (`repository_collector.py`)

The core component responsible for collecting repository data:

- Time-based and star-based collection strategies
- Progress tracking with resume capability
- Batch processing
- Error handling and targeted retry of failed items

The population, the procedure, and the completeness of the collection are described in
`docs/methodology/repository_collection.md`.

### 5. Owner Processor (`owners/owner_processor.py`)

Processes owner metadata (contributors and organizations):

- Batch processing of owner metadata
- Caching and deduplication
- Performance tracking

### 6. Orbis Enrichment (`enrichment/`)

Matches GitHub organizations against the Orbis (Bureau van Dijk) company
database and resolves a single headquarters entry per matched company. See
`CONTEXT.md` for the domain terminology and `docs/methodology/orbis_enrichment.md` for the
methodology.

- `orbis_client.py`: Oracle/Orbis database client
- `orbis_matching_logic.py`: candidate scoring and consensus detection
- `hq_detection.py`: headquarters resolution via a prioritized signal
  cascade
- `updater.py`: writes enrichment results back to the database, with
  dry-run support and transactional safety
- `scripts/enrichment/orbis_matching_pipeline.py` is the CLI entry point wiring these
  together against a live data source and sink

### 7. Geocoding Service (`geocoding/geocoding_service.py`)

Geocodes contributor and organization locations:

- Geocoding via Nominatim
- Caching of geocoding results
- Error handling

The procedure, its limits, and its validation are described in
`docs/methodology/geocoding.md`; the use of the resulting country in
`docs/methodology/country_attribution.md`.

### 8. Export (`export/csv_export.py`)

Exports database tables to CSV files.

### 9. User Interface (`ui/`)

Console output for progress and statistics:

- Progress display (`progress.py`)
- Statistics display (`stats.py`)

### 10. Command-Line Interface (`cli/`)

Implements the application's CLI commands:

- Repository collection (`collect_command.py`)
- Export (`export_command.py`)
- Geocoding (`geocoding_command.py`)
- Cleanup (`cleanup_command.py`)
- Reset (`reset_command.py`)

### 11. Utilities (`utils/`)

- Logging configuration (`logging_config.py`)
- Performance tracking (`performance_tracker.py`)

## Data Flow

1. The user invokes a command through the command-line interface.
2. The CLI initializes the required components and starts the operation
   (collection, enrichment, geocoding, or export).
3. The repository collector retrieves repository data from the GitHub API.
4. The owner processor processes contributor/organization metadata.
5. Collected data is persisted to the database.
6. The geocoding service geocodes owner locations; the Orbis enrichment
   pipeline, run separately, matches organizations to Orbis companies and
   resolves their headquarters.
7. Data can be exported to CSV via the export component.

## Dependencies

External libraries used by the application:

- **SQLAlchemy**: ORM for database access
- **Requests**: HTTP requests to the GitHub REST API
- **python-dotenv**: loading environment variables
- **geopy**: location geocoding
- **pycountry**: country codes and names
- **oracledb**: Orbis/Oracle database access
- **keyring**: OS-level credential storage for API tokens (via
  `utils/secrets.py`); **cryptography** is not imported directly but is
  pinned as a floor version for a transitive dependency (security fix, see
  `pyproject.toml`)

## Extensibility

The application is designed to be extended via new modules rather than
modification of existing ones:

1. New data sources: additional API clients and collector modules.
2. New export formats: additional export modules.
3. New enrichment sources: additional modules under `enrichment/`.
4. New CLI commands: additional modules under `cli/`.

## Performance

1. **API response caching** reduces the number of API calls.
2. **Token pooling** allows using multiple API tokens concurrently.
3. **Batch processing** reduces per-item overhead.
4. **Performance tracking** (`utils/performance_tracker.py`) records timing
   and API metrics per run under `logs/performance/`, enabling bottleneck
   analysis.

## Error Handling

- Individual failures in GraphQL enrichment are logged per repository
  without aborting the batch.
- Failed items are logged and can be retried individually (e.g.
  `enrich_repository_stats.py --retry-failed <file>`).
- Stack traces and detailed error causes are recorded in the log files.
- Transient API errors (e.g. HTTP 5xx) are retried automatically.
- Collection can resume after an error or interruption rather than
  restarting from scratch.
- Rate-limit handling waits automatically when a limit is exceeded.
