# GitHub Data Collector

An ETL system for collecting GitHub data on repositories, contributors, and
organizations, and storing it in a SQLite or MySQL database for downstream
analysis.

## Scope

The project covers four stages:

1. Collection of GitHub repository metadata and statistics
2. Collection of contributor and organization data, including location
   fields
3. Geocoding of location data to derive country and region information
4. Export of collected data to CSV for external analysis

An optional enrichment stage matches GitHub organizations against the Orbis
(Bureau van Dijk) company database to add headquarters location and other
firmographic data; see `CONTEXT.md` for the terminology used throughout that
component and `docs/methodology/orbis_enrichment.md` for the matching and
headquarters-resolution methodology.

The methodology of the collection pipeline is documented in three files:
`docs/methodology/repository_collection.md` (population, procedure, completeness),
`docs/methodology/geocoding.md` (derivation of a country from the profile location, limits,
validation), and `docs/methodology/country_attribution.md` (unit of analysis, country of a
repository, sources of a country).

## Features

- GitHub REST and GraphQL API integration for repository, contributor, and
  organization metadata
- Geographic enrichment via geocoding of contributor and organization
  locations
- Star-based and time-based collection strategies
- Token-pool management for GitHub API rate limits, with request caching and
  resumable progress tracking
- CSV export of collected data

## Getting Started

### Prerequisites

- Python 3.9+
- One or more GitHub API tokens
- A SQLAlchemy-compatible database (SQLite or MySQL)

### Installation

```bash
# Clone the repository
git clone https://github.com/maiklovenotwar/ISI-Github-Collector.git
cd ISI-Github-Collector

# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"

# Configure environment variables
cp .env.template .env
# Edit .env and add a GitHub API token
```

### Basic Usage

```bash
# Collect GitHub repositories
python scripts/collection/collect_repositories.py --time-range month --min-stars 100

# Geocode location data
python scripts/enrichment/update_location_geocoding.py

# Export data to CSV files
python scripts/export/export_tables_to_csv.py
```

See `docs/usage.md` for the full command reference.

### Example: Repositories per Country

The following sequence performs a complete run against a local SQLite
database, from collection to a first aggregate query:

```bash
# .env
# GITHUB_API_TOKEN=<your token>
# DATABASE_URL=sqlite:///data/github_data.db

# 1. Collect up to 500 repositories created in the last week with 100+ stars
python scripts/collection/collect_repositories.py --non-interactive --time-range week --min-stars 100 --limit 500

# 2. Derive country codes from the owners' free-text location fields
python scripts/enrichment/update_location_geocoding.py --no-interactive-filters

# 3. Check what was collected
python scripts/collection/collect_repositories.py --stats

# 4. Export all tables to exports/*.csv
python scripts/export/export_tables_to_csv.py --tables all
```

The resulting database can be queried directly. The following query returns
the ten countries whose organizations own the largest number of collected
repositories:

```sql
SELECT o.country_code,
       COUNT(*)                AS repositories,
       SUM(r.stargazers_count) AS stars
FROM repositories r
JOIN organizations o ON o.id = r.organization_id
WHERE o.country_code IS NOT NULL
GROUP BY o.country_code
ORDER BY repositories DESC
LIMIT 10;
```

Further examples, covering custom date ranges, star ranges, the GraphQL
enrichment scripts, and the Orbis matching pipeline, are given in
`docs/usage.md`.

## Database Configuration

Supported backends:
- SQLite: `DATABASE_URL=sqlite:///data/github_data.db`
- MySQL: `DATABASE_URL=mysql+mysqlconnector://user:pass@localhost/github_data`

There is no SQLite fallback. If `DATABASE_URL` is not set, the application
builds a MySQL connection from `DB_HOST`/`DB_PORT`/`DB_USER`/`DB_PASSWORD`/
`DB_NAME` instead (default: `root@localhost:3306/github_data`, no password).
SQLite is only used when `DATABASE_URL` is set explicitly.

`DATABASE_URL` can be set in `.env`; all scripts read it automatically. Most
scripts also accept a `--db-path` argument, which is converted to a
SQLAlchemy URL automatically where needed.

## Data Model

Three main tables:

1. **Contributors**: GitHub users, with location information
2. **Organizations**: GitHub organizations, with location information
3. **Repositories**: GitHub repositories, with metadata and statistics

See `docs/architecture/database.md` and `docs/architecture/schemas.md` for the full schema.

## Project Structure

```
ISI-Github-Collector/
├── .github/                # CI workflow, issue and pull request templates
├── config/                 # Matching thresholds and blog-domain blacklist
├── docs/
│   ├── architecture/        # System design, database schema, API client
│   ├── methodology/         # Collection, geocoding, country attribution, Orbis matching
│   ├── decisions/           # Recorded project decisions
│   ├── usage.md             # Full command reference with examples
│   └── testing.md           # Test suite layout and markers
├── scripts/                # Executable entry points, see scripts/README.md
│   ├── collection/      # Repositories and the per-repository contributor network
│   ├── enrichment/      # Profiles, repository statistics, geocoding, ccTLD, Orbis
│   ├── export/          # CSV export
│   ├── maintenance/     # Database reset, cleanup, geocoding benchmark
│   ├── diagnostics/     # Manual checks against the live Orbis database
│   └── migrations/      # Database schema migrations
├── src/
│   └── github_collector/   # Main package
│       ├── api/             # GitHub API integration
│       ├── cli/             # Command implementations behind the scripts
│       ├── database/        # Database models and operations
│       ├── enrichment/      # Orbis matching & HQ resolution
│       ├── export/          # CSV export
│       ├── geocoding/       # Geocoding services
│       ├── monitoring/      # Prometheus metrics
│       ├── owners/          # Contributor and organization processing
│       ├── schemas/         # Pydantic validation models
│       ├── ui/              # Console progress and statistics output
│       └── utils/           # Logging, retry, secrets, performance tracking
├── tests/                  # Automated tests, mirroring the package layout
├── workstreams/            # Ongoing analyses on the collected data, see workstreams/README.md
│   ├── entity_classification/  # Classification of organizations by type
│   └── orbis_matching/         # Validation of the Orbis matching
├── .env.template           # Environment variable template
├── pyproject.toml          # Project dependencies and tool configuration
├── CONTEXT.md              # Domain glossary for Orbis matching
├── CONTRIBUTING.md         # Development workflow
└── CHANGELOG.md
```

## Running Tests

With the dev dependencies installed (`pip install -e ".[dev]"`, see above):

```bash
pytest tests/
```

With coverage:

```bash
pytest --cov=github_collector
```

### Orbis Integration Tests

Orbis integration tests require a live connection to the Orbis Oracle
database and are disabled by default.

```bash
# Default test run (no Orbis integration)
pytest tests/

# Including Orbis integration tests (requires VPN)
pytest tests/ -m "orbis_integration or not orbis_integration"

# Orbis integration tests only
pytest tests/integration/test_orbis_connection.py -m orbis_integration -v
```

Prerequisites for the Orbis tests:
1. An active VPN connection to the institute network
2. `.env` configured with valid `ORBIS_*` credentials
3. Network access to the Oracle/Orbis host

## Contributing

`CONTRIBUTING.md` specifies the branching and review workflow, the code
style, and the data policy. Changes are recorded in `CHANGELOG.md`. Defects
and proposals are tracked as issues in the repository.

## License

This project is licensed under the MIT License — see [LICENSE](LICENSE) for
details.
