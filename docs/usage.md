# GitHub Data Collector - Usage Guide

This guide documents the command-line interface of the GitHub Data Collector: installation,
data collection, the enrichment steps, export, and configuration. Each section states the
purpose of a script and gives representative invocations.

---

## Installation

### Prerequisites
- Python 3.9 or higher
- pip (Python package manager)
- Git (for cloning the repository)
- GitHub API Token (for accessing the GitHub API)

### Setup Steps

1. **Clone Repository**
```bash
git clone https://github.com/maiklovenotwar/ISI-Github-Collector.git
cd ISI-Github-Collector
```

2. **Create Virtual Environment**
```bash
python -m venv venv

# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate
```

3. **Install Dependencies**
```bash
pip install -e ".[dev]"
```

4. **Configure Environment**
Create a `.env` file in the project root:
```bash
# GitHub API Token (required)
GITHUB_API_TOKEN=your_github_token

# Multiple tokens (optional, comma-separated)
GITHUB_API_TOKENS=token1,token2,token3

# Database URL (required -- no SQLite fallback; unset falls back to a MySQL
# connection on localhost, see "Database Configuration" below)
DATABASE_URL=sqlite:///data/github_data.db

# Performance tracking (optional, default: true)
ENABLE_PERFORMANCE_TRACKING=true

# Log level (optional, default: INFO)
LOG_LEVEL=INFO
```

5. **Verify Installation**
```bash
python scripts/collection/collect_repositories.py --stats
```

---

## Data Collection

### Repository Collection

**Basic Usage:**
```bash
python scripts/collection/collect_repositories.py
```

**Non-Interactive Mode:**
```bash
python scripts/collection/collect_repositories.py --non-interactive --time-range week --limit 1000 --min-stars 100
```

**Interrupted and repeated runs.** Progress is stored in `data/collection_state.json`,
together with the mode and the star range of the collection. A run that ends with an error
(exit code 1) names the period at which it stopped; the same call resumes there. A call with
other arguments starts a new collection if the recorded one has finished, and is refused if it
has not; in that case finish the interrupted collection first, or rename or delete the state
file. A state file written by a version before 2026-10-13 records no arguments and is refused
in the same way.

**Checking a collection.** Every finished period is recorded in `logs/collection_audit.jsonl`.
`python scripts/diagnostics/summarize_collection_audit.py` summarizes the file and exits with
code 1 if a period needs attention; see `docs/methodology/repository_collection.md`.

### Parameters

#### Time Range Options
- `--time-range`: Predefined range (`week`, `month`, `year`, `custom`)
- `--start-date`: Start date for custom range (YYYY-MM-DD)
- `--end-date`: End date for custom range (YYYY-MM-DD, inclusive)

#### Collection Options
- `--limit`: Maximum number of repositories to collect
- `--all`: Collect all available repositories
- `--min-stars`: Minimum star count
- `--star-range`: Explicit star-count range for star-based collection strategies

#### Database Options
- `--db-path`: Database file path (SQLite)
- `--analyze`: Analyze the database instead of collecting

#### Output Options
- `--non-interactive`: Run without user prompts
- `--stats`: Show database statistics only
- `--enable-metrics`: Expose Prometheus metrics during the run
- `--metrics-port`: Port for the Prometheus metrics endpoint

### Examples

**Collect recent repositories:**
```bash
python scripts/collection/collect_repositories.py --time-range month --min-stars 50
```

**Custom date range:**
```bash
python scripts/collection/collect_repositories.py --start-date 2024-01-01 --end-date 2024-12-31 --limit 5000
```

**Collect everything, with metrics exposed:**
```bash
python scripts/collection/collect_repositories.py --all --non-interactive --enable-metrics --metrics-port 9100
```

---

## Data Enrichment

### Repository Statistics Enrichment

**Basic Usage:**
```bash
python scripts/enrichment/enrich_repository_stats.py
```

**With Parameters:**
```bash
python scripts/enrichment/enrich_repository_stats.py --batch-size 50 --limit 1000
```

**Retry only previously failed repos:**
```bash
python scripts/enrichment/enrich_repository_stats.py --retry-failed failed_repos.txt
```

### Organization Data Enrichment

**GraphQL-based enrichment:**
```bash
python scripts/enrichment/enrich_org_data_graphql.py --batch-size 20 --limit 500
```

### Contributor Data Enrichment

The script fetches profile data (name, location, company, and related fields) for
contributors whose profile has not been fetched before, and records every fetch in
`contributors.enrichment_checked_at`. `--scope` restricts the run to contributors of the
per-repository contributor network (`new`), to all others (`gap`), or to both (`all`, default).

```bash
# Fetch the profiles without writing anything
python scripts/enrichment/enrich_contributor_profiles.py --dry-run --limit 100

# Enrich all contributors that have not been fetched yet
python scripts/enrichment/enrich_contributor_profiles.py

# Two parallel processes on disjoint halves, each with its own token
python scripts/enrichment/enrich_contributor_profiles.py --token-index 0 --shard-index 0 --shard-count 2
python scripts/enrichment/enrich_contributor_profiles.py --token-index 1 --shard-index 1 --shard-count 2
```

### Contributor Network

The contributor network records, for each repository in a range of star counts, the
accounts that contributed to it (table `repository_contributors`). One script runs the
four steps in order: collection of the contributor lists, creation of rows for accounts
not yet known, profile enrichment, and geocoding. The procedure and its limits are
described in [methodology/contributor_network.md](methodology/contributor_network.md).

```bash
# Request the contributor lists for a small sample without writing anything;
# the dry run sends requests to the GitHub API and stops after the first step
python scripts/collection/collect_contributor_network.py --min-stars 500 --max-stars 1000 --limit 10 --dry-run

# Collect and enrich all repositories with 500 to 999 stars
python scripts/collection/collect_contributor_network.py --min-stars 500 --max-stars 1000

# Repeat only profile enrichment and geocoding after an interrupted run
python scripts/collection/collect_contributor_network.py --min-stars 500 --max-stars 1000 --skip-collect --skip-stubs
```

Three migrations must have been applied before the first run:
`scripts/migrations/add_repository_contributors_table.sql`,
`add_repository_contributors_github_id.sql`, and, for the profile enrichment,
`add_contributors_enrichment_checked_at.sql`. With `--dry-run` only the first step runs,
without writing; the three writing steps are never started in a dry run. The collection and the creation of
missing accounts write with MySQL-specific statements; on other databases they run only
with `--dry-run`. The output of each step is written to a separate file in the log
directory.

### Geocoding

**Location-based enrichment** (uses the Nominatim/OpenStreetMap geocoder; there is no
provider switch -- see `--user-agent` below for Nominatim's usage-policy requirement):
```bash
python scripts/enrichment/update_location_geocoding.py --contributors --limit 500
```

**Useful options:**
- `--contributors` / `--organizations`: Restrict geocoding to one entity type (default: both)
- `--min-stars` / `--max-stars`: Only geocode organizations within a star-count range
- `--created-since` / `--created-until`: Only geocode entities created in a date range
- `--user-agent`: Custom User-Agent string for the Nominatim API (required by its usage policy)
- `--cache-file`: Path to the local geocoding cache (default: `geocoding_cache.json`)
- `--clear-cache`: Clear the geocoding cache before running
- `--show-missing`: List entities with a location that could not be geocoded
- `--force`: Re-geocode entities that already have a country code
- `--retry-empty`: Ask again for locations that an earlier run could not resolve: removes cache
  entries without a country and also selects rows that were marked as not geocodable

A location is marked as not geocodable (empty country code) only when the geocoder answered
without a match. If the geocoder did not answer (timeout, rate limit, service error), nothing
is stored and the row stays open. The run then ends with exit code 1 and reports the number of
unanswered requests; after ten unanswered requests in a row it stops early. Calling the script
again processes the rows that stayed open. In `collect_contributor_network.py` this makes step 4
fail although part of the rows were geocoded; repeat it with
`--skip-collect --skip-stubs --skip-enrich`.

### ccTLD Enrichment

The script derives an ISO country code from the country-code top-level
domain of the `blog` or `email` field and writes it to `country_code_cctld`.
It first validates the extraction against rows that already carry a geocoded
country code and aborts if the agreement rate is below 80%. Without
`--write`, no changes are made.

```bash
# Dry run for organizations (default)
python scripts/enrichment/enrich_cctld.py

# Dry run for contributors
python scripts/enrichment/enrich_cctld.py --entity contributors

# Validation against geocoded rows only
python scripts/enrichment/enrich_cctld.py --validate-only

# Write the results for at most 1,000 organizations
python scripts/enrichment/enrich_cctld.py --write --limit 1000

# Recompute stored assignments under the current rules (dry run, then write)
python scripts/enrichment/enrich_cctld.py --entity contributors --recheck
python scripts/enrichment/enrich_cctld.py --entity contributors --recheck --write
```

Each writing run prints the SQL statement that reverts it, identified by the
run identifier stored in `cctld_run_id`. `--recheck` reports each change of
country (for example `EE -> -`) and writes only rows whose result differs.
Its run identifier marks the changed rows, but resetting them would not
restore the previous values; a copy of the affected rows taken before the
run is needed for that.

The columns written by each enrichment script are listed in
`docs/architecture/database.md` ("Write Paths of the Enrichment Scripts").

---

## Orbis Enrichment

Matches GitHub organizations against companies in the Orbis (Bureau van Dijk) database
to enrich them with HQ country/city, legal name, etc. The matching logic itself lives in
`src/github_collector/enrichment/orbis_matching_logic.py` and `hq_detection.py`;
`scripts/enrichment/orbis_matching_pipeline.py` is the CLI entry point that wires them up to a real
data source (MySQL `organizations`) and sink (`organizations` result columns +
`orbis_enrichments` history). See `CONTEXT.md` for the domain vocabulary
(Match-Status/Match-Type, HQ-Auflösung) used throughout.

**Dry run against a limited sample (no DB writes):**
```bash
python scripts/enrichment/orbis_matching_pipeline.py --dry-run --limit 50
```

**Targeted run against specific GitHub logins:**
```bash
python scripts/enrichment/orbis_matching_pipeline.py --logins airbus,IBM,Netflix --dry-run
```

**Real run, writing results to the database:**
```bash
python scripts/enrichment/orbis_matching_pipeline.py --limit 1000
```

**Export the outcome for every organization as CSV**, in a dry run or a writing run. The
file contains Orbis data and must go to a path Git ignores (the script refuses others);
next to it, `<name>.meta.json` records the run identifier, the Git commit, the hash of
`config/orbis_matching.yaml`, the Orbis schema, the counts per Match-Status and whether
the Latin name column was read:
```bash
python scripts/enrichment/orbis_matching_pipeline.py --dry-run --limit 1000 \
    --export-csv workstreams/orbis_matching/data/dry_run.csv
```

**Undo a previous run:**
```bash
python scripts/enrichment/orbis_matching_pipeline.py --rollback <run-id>
```

**Other options:**
- `--min-name-similarity`: Override the name-similarity threshold from `config/orbis_matching.yaml`

---

## Data Export

### CSV Export

**Export all tables to CSV:**
```bash
python scripts/export/export_tables_to_csv.py --output-dir exports/
```

**Export specific tables:**
```bash
python scripts/export/export_tables_to_csv.py --tables repositories organizations --output-dir exports/
```

The tables are read in blocks of 10,000 rows ordered by `id` and written
as they are read, so that even `repositories` does not have to fit into
memory. Each file is written under a temporary name and renamed when the
table is complete. If the export of a table fails, no file is left under
its name, the other tables are still exported, and the script ends with
exit code 1.

### Content of the Export Files
- **repositories.csv**: repository metadata and statistics
- **organizations.csv**: organization information
- **contributors.csv**: account data
- **repository_contributors.csv**: the contributors of each repository with their number of
  contributions
- **orbis_enrichments.csv**: the log of Orbis matches per organization and run

The files contain the raw columns of these tables. They include personal
data that account holders entered in their GitHub profiles (name, e-mail
address, biography, location), and for anonymous contributors the e-mail
address or name recorded with the commits; handle and share them
accordingly. `orbis_enrichments.csv` contains data taken from Orbis, whose
licence restricts passing them on. The files do not contain a country
per repository: the rule that assigns a country to a repository is
described in `docs/methodology/country_attribution.md` and has to be
applied to the exported columns.

---

## Monitoring and Troubleshooting

### Performance Monitoring

There is no separate performance-analysis script. When `ENABLE_PERFORMANCE_TRACKING`
is on (the default), every collection/enrichment run writes its metrics as JSON files
under `logs/performance/` via `PerformanceTracker` -- inspect those files directly.

### Error Handling

| Symptom | Cause | Remedy |
|---------|-------|--------|
| Rate-limit errors | The hourly request budget of all configured tokens is exhausted | Configure additional tokens in `GITHUB_API_TOKENS`, or reduce the batch size |
| HTTP 401 responses | A token is invalid or expired | The token is removed from the rotation automatically; the run stops only when no valid token remains |
| Database lock errors (SQLite) | Concurrent writers on the same database file | Reduce the batch size or run the scripts sequentially |
| Network timeouts during statistics enrichment | Transient API failures | Re-run the failed repositories with `--retry-failed` (see below) |

### Logging

**Log files location** (see `LOG_CONFIG` in `src/github_collector/config.py`):
- `logs/collection/repository_collection.log`: Repository collection logs
- `logs/export/export.log`: Export logs
- `logs/geocoding/geocoding.log`: Geocoding logs
- `logs/maintenance/`: Maintenance/admin task logs (reset, cleanup)
- `logs/performance/`: Performance tracking output (JSON)

### Retry Failed Operations

**Resume failed operations** (pass the file path directly to `--retry-failed`):
```bash
python scripts/enrichment/enrich_repository_stats.py --retry-failed failed_repos.txt
```

---

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GITHUB_API_TOKEN` | Primary GitHub API token | Required |
| `GITHUB_API_TOKENS` | Multiple tokens (comma-separated) | Optional |
| `DATABASE_URL` | Database connection string | see below -- no SQLite fallback |
| `LOG_LEVEL` | Logging level | `INFO` |
| `ENABLE_PERFORMANCE_TRACKING` | Enable performance tracking | `true` |
| `ORBIS_LATIN_NAME_COLUMN` | Name of the Latin company-name column in Orbis; kept out of the repository (licensed schema). Without it, non-Latin names are compared only through the transliteration | unset |

### Database Configuration

**There is no zero-config default.** If `DATABASE_URL` is not set, the application falls
back to building a MySQL connection string from `DB_HOST`/`DB_PORT`/`DB_USER`/
`DB_PASSWORD`/`DB_NAME` (defaults: `root@localhost:3306/github_data`, no password) --
it does **not** fall back to SQLite. Set `DATABASE_URL` explicitly for either case:

**SQLite:**
```
DATABASE_URL=sqlite:///data/github_data.db
```

**MySQL** (uses the `mysql-connector-python` driver, not PyMySQL):
```
DATABASE_URL=mysql+mysqlconnector://user:password@localhost/github_data
```

### API Rate Limits

- **GitHub REST API**: 5,000 requests per hour and token
- **GitHub GraphQL API**: 5,000 points per hour and token
- **Nominatim (geocoding)**: governed by the Nominatim usage policy, which requires an
  identifying User-Agent (`--user-agent`); requests are throttled by the built-in rate
  limiter and answered from the local cache where possible

Rate limits apply per token. Configuring several tokens in `GITHUB_API_TOKENS` raises the
total request budget proportionally, because the token pool selects the token with the
largest remaining budget for each request.

---

## Database Reset

`scripts/maintenance/reset_and_collect.py` deletes the configured database, recreates the schema, and
then starts `scripts/collection/collect_repositories.py`. Because the operation is destructive, it has to
be confirmed with the `--yes` flag; an invocation without it exits with an error before any
data is touched. This applies to SQLite and MySQL targets alike.

Two environment variables further control the behavior:

| Variable | Effect |
|----------|--------|
| `GITHUB_COLLECTOR_DRY_RUN=true` | Logs the actions that would be performed without executing them; `--yes` is not required in this mode |
| `GITHUB_COLLECTOR_ALLOW_MYSQL_RESET=true` | Required for MySQL targets in addition to `--yes`; without it the script refuses to run `DROP DATABASE` and exits with an error |

```bash
# Inspect what a reset would do
GITHUB_COLLECTOR_DRY_RUN=true python scripts/maintenance/reset_and_collect.py

# Delete the configured database, recreate the schema, and start the collection
python scripts/maintenance/reset_and_collect.py --yes
```

---

## Maintenance Tools

| Script | Purpose |
|--------|---------|
| `scripts/maintenance/cleanup_orphaned_owners.py` | Removes contributors and organizations that are no longer referenced by any repository; reports only, unless `--write` is given |
| `scripts/maintenance/manage_gold_standard.py` | Samples and labels locations for the geocoding gold standard (`tests/resources/geocoding_gold_standard.json`) |
| `scripts/maintenance/benchmark_geocoding.py` | Measures the accuracy of the geocoding service against the gold standard |
| `scripts/maintenance/clone_schema.py` | Copies the table design of a MySQL database into an empty test database (see below) |
| `scripts/diagnostics/diagnose_hq_signals.py` | Reports the distribution of headquarters-resolution signals; requires Orbis access |
| `scripts/diagnostics/check_orbis_schema.py` | Lists the Orbis tables and columns available for headquarters resolution; requires Orbis access |

### Test Instance

A second MySQL server on the same machine lets every script run against real data without
touching the project database. It uses the same MySQL installation, its own data directory
(`data/mysql_test/`, ignored by Git) and port 3307, and it does not read the server's `my.ini`.

1. Read the settings of the project database (read only): `SHOW CREATE DATABASE`,
   `SHOW TRIGGERS`, and `SHOW VARIABLES` for `sql_mode`, `character_set_server`,
   `collation_server`, `lower_case_table_names`, `time_zone`, `innodb_strict_mode`,
   `group_concat_max_len`, `max_allowed_packet`, `explicit_defaults_for_timestamp`.
2. Initialise and start the server with these values; `--no-defaults` must be the first option:

   ```powershell
   mysqld --no-defaults --initialize-insecure --lower-case-table-names=<value> `
       --datadir=<repo>\data\mysql_test
   mysqld --no-defaults --basedir="C:\Program Files\MySQL\MySQL Server 8.0" `
       --datadir=<repo>\data\mysql_test --port=3307 --bind-address=127.0.0.1 --mysqlx=OFF `
       --enable-named-pipe=OFF --shared-memory=OFF --socket=MYSQL_TEST3307 `
       --shared-memory-base-name=MYSQL_TEST3307 --disable-log-bin `
       --tmpdir=<repo>\data\test_run\tmp --secure-file-priv=<repo>\data\test_run\uploads `
       --log-error=<repo>\data\test_run\mysqld.err <settings from step 1>
   ```

3. Connect only with `mysql --no-defaults --protocol=TCP -h 127.0.0.1 -P 3307 -u root`; without
   `--protocol=TCP` a client can reach the project server through a named pipe. Set a root
   password, create the database with the character set and collation of step 1, and a user
   that exists only on the test server, with rights on that database only.
4. Copy the tables (from a shell in which `DATABASE_URL` is not set to the test server):

   ```bash
   python scripts/maintenance/clone_schema.py --source-url <project database URL> \
       --target-url mysql+mysqlconnector://<test user>:<password>@127.0.0.1:3307/github_data
   ```

   The script copies every base table with `SHOW CREATE TABLE` (indexes, foreign keys and table
   options included; tables starting with `tmp_` are left out), refuses port 3306 and a target
   that already has tables, and only reads the source.
5. Run scripts with all paths redirected. Set every variable, including `CACHE_DIR`; scripts
   that find no value fall back to `.env` and the project directories:

   | Variable | Test value |
   |---|---|
   | `DATABASE_URL` | the target URL of step 4 |
   | `DATA_DIR` | `<repo>/data/test_run/data` |
   | `LOG_DIR` | `<repo>/data/test_run/logs` |
   | `CACHE_DIR` | a copy of `data/cache` in `<repo>/data/test_run/cache` |
   | `EXPORT_DIR` | `<repo>/data/test_run/exports` |

6. Stop the server with
   `mysqladmin --no-defaults --protocol=TCP -h 127.0.0.1 -P 3307 -u root -p shutdown`.
