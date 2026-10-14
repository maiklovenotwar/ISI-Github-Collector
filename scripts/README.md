# Scripts

Command-line entry points of the collector, grouped by pipeline stage. Each
script is started from the repository root, for example
`python scripts/collection/collect_repositories.py --help`. The logic of
most scripts lies in the package under `src/github_collector/`; the scripts
parse arguments and call it.

Detailed usage and examples are given in [`docs/usage.md`](../docs/usage.md);
the rows selected and the columns written by each enrichment step are listed
in [`docs/architecture/database.md`](../docs/architecture/database.md).

The column *External access* names the systems a script contacts besides the
collector database.

## `collection/`

| Script | Purpose | Writes | External access |
|--------|---------|--------|-----------------|
| `collection/collect_repositories.py` | Collects repositories for a time range and a minimum star count, together with their owners. | `repositories`, `contributors`, `organizations` | GitHub API |
| `collection/collect_contributor_network.py` | Runs the four steps of the contributor network for one star range: the two scripts below, then profile enrichment and geocoding. | nothing itself; starts the scripts that write | through the scripts it starts |
| `collection/collect_repository_contributors.py` | Step 1 of the contributor network: requests the contributor list of each repository in a star range. MySQL only for writing. | `repository_contributors` | GitHub API |
| `collection/add_missing_contributors.py` | Step 2 of the contributor network: creates a minimal row for every account that appears in a contributor list but is not yet known. MySQL only for writing. | `contributors` (new rows), `repository_contributors.contributor_id` | none |

## `enrichment/`

| Script | Purpose | Writes | External access |
|--------|---------|--------|-----------------|
| `enrichment/enrich_repository_stats.py` | Adds commit and pull request counts to repositories. The contributor count is not collected by this script; it writes 0 into that column. | `repositories` (`commits_count`, `pull_requests_count`, `contributors_count`) | GitHub API |
| `enrichment/enrich_org_data_graphql.py` | Fetches the profile of organizations whose profile fields are empty. | `organizations` (profile columns, `enrichment_checked_at`) | GitHub API |
| `enrichment/enrich_contributor_profiles.py` | Fetches the profile of contributors that have not been fetched before. | `contributors` (profile columns, `enrichment_checked_at`) | GitHub API |
| `enrichment/update_location_geocoding.py` | Resolves the free-text location of contributors and organizations to a country and region. | `contributors`, `organizations` (`country_code`, `region`) | geocoding service |
| `enrichment/enrich_cctld.py` | Derives a country code from the country-code top-level domain of the website or e-mail address; `--recheck` recomputes stored assignments under the current rules; dry run unless `--write` is given. | `country_code_cctld` and three accompanying columns of `organizations` or `contributors` | none |
| `enrichment/orbis_matching_pipeline.py` | Matches organizations to companies in the Orbis database and resolves the headquarters country. | `organizations` (Orbis columns), `orbis_enrichments` | Orbis database (VPN) |

## `export/`

| Script | Purpose | Writes | External access |
|--------|---------|--------|-----------------|
| `export/export_tables_to_csv.py` | Exports database tables to CSV files. | CSV files in the export directory | none |

## `maintenance/`

| Script | Purpose | Writes | External access |
|--------|---------|--------|-----------------|
| `maintenance/reset_and_collect.py` | Empties the configured database and starts a repository collection. Refuses to run without `--yes`; MySQL additionally requires `GITHUB_COLLECTOR_ALLOW_MYSQL_RESET=true`. | deletes all collected data | GitHub API (through the collection it starts) |
| `maintenance/cleanup_orphaned_owners.py` | Removes contributors and organizations that no repository and no contributor list refers to. Without `--write` it only reports how many rows it would remove. | deletes rows from `contributors`, `organizations` | none |
| `maintenance/recheck_ambiguous_locations.py` | Re-evaluates stored locations that end in `Georgia`, `WA`, or `CA`, which earlier versions assigned to the United States without asking the geocoder; dry run unless `--write` is given. | `contributors`, `organizations` (`country_code`, `region`) | geocoding service |
| `maintenance/reset_stale_countries.py` | Resets `country_code` and `region` where the stored country no longer matches the stored location (the location changed after geocoding, or was never geocoded), so that the geocoding determines them anew; decides from the mapping table, the cache, and the text rules without sending requests. Dry run unless `--write` is given; `--write` first saves the affected rows as CSV in `data/backups/`. | `contributors`, `organizations` (`country_code`, `region`) | none |
| `maintenance/benchmark_geocoding.py` | Measures the accuracy of the geocoding against the gold-standard locations in `tests/resources/`. | nothing in the database | geocoding service |
| `maintenance/manage_gold_standard.py` | Maintains the gold-standard locations used by the geocoding benchmark. | `tests/resources/geocoding_gold_standard.json` | geocoding service |

## `diagnostics/`

Manual checks. The two Orbis scripts query the live Orbis database and require VPN
access; the fork count sends a few search requests to GitHub; the audit summary and the
cache summary read a local file only.

| Script | Purpose | Writes | External access |
|--------|---------|--------|-----------------|
| `diagnostics/check_orbis_schema.py` | Lists the Orbis tables and columns available for headquarters resolution. | nothing | Orbis database (VPN) |
| `diagnostics/diagnose_hq_signals.py` | Analyses the headquarters signals of organizations with several Orbis candidates. | nothing | Orbis database (VPN) |
| `diagnostics/summarize_collection_audit.py` | Summarizes the audit log of the repository collection (`logs/collection_audit.jsonl`) and lists the periods in which fewer repositories arrived than GitHub announced, a response was marked incomplete, or the result was truncated. | nothing | none |
| `diagnostics/inspect_geocoding_cache.py` | Counts the entries of the local geocoding cache with and without a country and separates the entries without a country into those the current rules resolve, those recognised as not a place, and the rest. | nothing | none |
| `diagnostics/count_excluded_forks.py` | Reports, for several star thresholds, how many forks and how many non-fork repositories the GitHub search announces; forks are not part of the collected population. Two search requests per threshold. | nothing | GitHub API |

`diagnostics/methodology_checks.sql` contains read-only queries that support the figures in
`docs/methodology/` (reference date, repositories per month of creation, accounts with a
location and a country, the two rows of an organization, countries by source).

## `migrations/`

Schema changes, applied manually. There is no migration framework. The SQL
files state their date and purpose, and most also state how to revert the
change. They are listed below in the order of their dates; where two files
share a date, the one listed first must be applied first.

| Script | Purpose | Writes | External access |
|--------|---------|--------|-----------------|
| `migrations/add_cctld_contributors_no_index.py` | Adds the ccTLD columns to `contributors` without creating an index. | schema of `contributors` | none |

SQL files:

- `add_orbis_columns.sql` (2026-02-19): Orbis enrichment columns on `organizations`.
- `add_cctld_columns.sql` (2026-02-24): ccTLD columns on `organizations`.
- `add_orbis_enrichments_table.sql` (2026-08-07): table `orbis_enrichments`.
- `add_repository_contributors_table.sql` (2026-08-08): table `repository_contributors`.
- `add_repository_contributors_github_id.sql` (2026-08-08): columns `github_user_id` and
  `github_type` on `repository_contributors`.
- `add_orbis_companies_table.sql` (2026-08-09): table `orbis_companies` (later merged, see
  `consolidate_orbis_enrichments.sql`).
- `add_orbis_enrichment_history_tracking.sql` (2026-08-09): turns `orbis_enrichments` into a
  permanent log and adds the reference to the current match on `organizations`.
- `consolidate_orbis_enrichments.sql` (2026-08-09): moves the company data into
  `orbis_enrichments` and drops `orbis_companies`.
- `add_contributors_enrichment_checked_at.sql` (2026-08-27): column `enrichment_checked_at`
  on `contributors`.
- `add_organizations_enrichment_checked_at.sql` (2026-10-09): column
  `enrichment_checked_at` on `organizations`.
