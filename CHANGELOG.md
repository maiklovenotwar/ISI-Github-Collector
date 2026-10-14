# Changelog

Notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- Methodology of the collection pipeline: `docs/methodology/repository_collection.md`,
  `docs/methodology/geocoding.md`, and `docs/methodology/country_attribution.md`. They state the
  population (forks are excluded), the reference date, the measures and limits regarding
  completeness, the limits and the validation of geocoding, and the rules for counting accounts
  and assigning a country to a repository.
- `scripts/diagnostics/count_excluded_forks.py` and `scripts/diagnostics/methodology_checks.sql`
  provide figures that support these documents.
- `update_location_geocoding.py --retry-empty` asks again for locations that an earlier run
  could not resolve. New cache entries record their source (mapping table, text heuristic,
  Nominatim, or no result) and the time they were written.
- Audit log of the repository collection: one JSON line per finished period in
  `logs/collection_audit.jsonl` with the number of results GitHub announced, the number
  collected, and the shortfall. `scripts/diagnostics/summarize_collection_audit.py` summarizes
  it and exits with code 1 if a period needs attention.
- Search responses that GitHub marks as `incomplete_results`, and periods that exceed 1,000
  results but cannot be split further, are logged and recorded in the collection state.
- `scripts/README.md` and `workstreams/README.md`: one line per script with its purpose,
  what it writes, and what it needs. A test fails when a script is missing from, or no
  longer matches, these overviews.
- `docs/methodology/contributor_network.md`: procedure, identification of accounts,
  limits, and coverage of the per-repository contributor network.
- `add_missing_contributors.py --dry-run`, which reports the number of accounts that would
  be created without writing.
- Continuous integration (`.github/workflows/ci.yml`): pre-commit hooks and the test suite run
  on every pull request.
- Pull request and issue templates, `CODEOWNERS`, Dependabot configuration, and an extended
  `CONTRIBUTING.md` describing the branching, review, and release workflow.
- `--entity {organizations,contributors}` option for `scripts/enrichment/enrich_cctld.py`, and unit tests
  for the ccTLD extraction rules.
- Invalid GitHub tokens (HTTP 401) are removed from the token-pool rotation instead of being
  retried indefinitely.
- `contributors.enrichment_checked_at` distinguishes "profile never fetched" from "fetched,
  nothing found".
- Wildcard entries for hosting and blogging platforms in `config/blog_blacklist.txt`.
- `scripts/enrichment/enrich_contributor_profiles.py`, which fetches contributor profiles in bulk,
  records every fetch in `contributors.enrichment_checked_at`, supports parallel runs with
  several tokens (`--shard-index`/`--shard-count`), and runs on SQLite as well as MySQL. It was
  developed as `enrich_contributor_locations.py` in the `github_micro_network_data` workstream.
- `organizations.enrichment_checked_at` (migration
  `scripts/migrations/add_organizations_enrichment_checked_at.sql`).

### Changed

- `scripts/diagnostics/methodology_checks.sql` reads the `repositories` table in full twice
  instead of seven times: one statement counts everything that needs no join, one joins the
  owners. Months are grouped by number, which the server can handle on the full table.
- The methodology documents state the figures measured in the project database on 2026-10-13:
  retrieval dates, share of accounts and repositories with a country, the two rows per
  organization, the ccTLD step, `repositories.organization_id`, and repositories per month of
  creation.
- The two remaining workstreams are named `workstreams/entity_classification/` and
  `workstreams/orbis_matching/` (previously prefixed with `github_`). Their scripts are named
  after what they produce: `build_goldstandard_samples.py` (was `merge_goldstandard.py`),
  `classify_organizations_by_rules.py` (was `classify_full_db.py`),
  `evaluate_rules_on_goldstandard.py` (was `classify_against_goldstandard.py`),
  `evaluate_llms_on_goldstandard.py` (was `local_llm_compare.py`),
  `plot_goldstandard_distribution.py` (was `build_goldstandard_chart.py`), and
  `compare_pipeline_with_reviewed_batches.py` (was `validate_against_batches.py`). The result
  files of the rule-based run are `results/rule_classification_summary.txt` and
  `results/rule_classification_examples.csv`.
- The contributor-network scripts moved from `workstreams/github_micro_network_data/` to
  `scripts/collection/`: `run_collection_pipeline.py` is now
  `collect_contributor_network.py`, `create_contributor_stubs.py` is now
  `add_missing_contributors.py`, and `collect_repository_contributors.py` keeps its name.
  Step output is written to the log directory. The two scripts that write refuse to do so
  on a database other than MySQL.
- `collect_contributor_network.py --dry-run` never starts the three writing steps. Previously,
  combining it with `--skip-collect` ran them without a dry-run flag.
- The entry points under `scripts/` are grouped by pipeline stage: `collection/`, `enrichment/`,
  `export/`, `maintenance/`, `diagnostics/` and `migrations/`. Every script keeps its file name;
  only the folder changed (for example `scripts/collection/collect_repositories.py`).
- `pytest` runs without extra flags: the manual Oracle diagnostics moved from `tests/orbis/`
  to `scripts/diagnostics/`, and `src/` is on the test path by default.
- `ruff` and `black` skip `workstreams/` consistently, both in `pyproject.toml` and in the
  pre-commit hooks.
- Oracle calls time out after three minutes instead of blocking indefinitely on a stalled
  connection; the Orbis matching pipeline logs its progress at INFO level.
- The ccTLD extraction rules moved from the script into
  `src/github_collector/enrichment/cctld.py`.
- The minimum supported Python version is 3.9 (previously declared as 3.8, which was not
  tested).
- `scripts/maintenance/reset_and_collect.py` requires the `--yes` flag before it deletes a database;
  previously an invocation without arguments, or with `--help`, started the reset immediately.

### Fixed

- `enrich_org_data_graphql.py` wrote its cache and log file into the current working directory;
  they are now stored under `data/cache/` and `logs/`.
- The CSV export loaded a whole table into memory and ended with success even when a table
  could not be exported. Tables are now read in blocks and written as they are read; a failed
  table leaves no file under its name and makes the script end with exit code 1.
- `enrich_repository_stats.py` wrote 0 into `contributors_count`, which it does not collect; the
  column now stays `NULL`. Results are written after each batch instead of once at the end, so an
  aborted run keeps them. A database error is no longer reported as one updated repository, a
  checkpoint file left in the working directory no longer skips a batch, and the script ends
  with exit code 1 if a whole batch got no answer (repositories that GitHub no longer knows are
  listed in the file of failed IDs but do not fail the run).
- When a profile or an owner was stored again with a different location, the country determined
  for the old location was kept. A changed location now resets `country_code` and `region`, so
  that the geocoding determines them anew.
- Geocoding read the cache before the table of special cases, so a corrected entry never took
  effect for a string that was already cached: `Korea, South` stayed assigned to Australia
  through an old geocoder match. An entry of the table that names a country now takes precedence
  over the cache.
- The ccTLD step assigned countries to service domains and to endings without a link to a
  country: `linktr.ee` made most of the accounts assigned to Estonia, `notion.so` many of those
  assigned to Somalia, and `youtu.be` part of those assigned to Belgium. Service domains
  (`linktr.ee`, `buymeacoff.ee`, `coff.ee`, `lin.ee`, `notion.so`, `youtu.be`, link shorteners
  and free hosting such as `rb.gy`, `sr.ht`, and `free.nf`) and the endings
  `.tk`, `.ml`, `.ga`, `.cf`, `.gq`, `.pw`, `.re`, `.pm`, `.tf`, and `.st` now yield no country.
  The validation of `enrich_cctld.py` also reports the countries with the lowest agreement.
- `cleanup_orphaned_owners.py` treated an organization as orphaned when no repository named it in
  `organization_id`. That column is not set for repositories stored from a search result before
  2026-10-13, so the script would have deleted organizations that own repositories (410,884 in
  the project database). Ownership through `owner_id` now counts as well. The script deletes
  only with `--write` and otherwise reports the number of rows it would remove; it also no
  longer ends with an error after a successful run.
- The collector wrote repositories through the global database session, which is bound to the
  database of the configuration, instead of the database it was created for. With `--db-path`,
  or in a test with its own database, the rows went to the configured database. The collector
  now uses the session of its own database.
- The test suite could reach the configured database: two integration tests ran the
  collection script and thereby wrote their fixture repositories (ids 1 and 42) into it. The
  whole test process now runs on a temporary SQLite database that is set before the package
  is imported.
- Geocoding assigned every location ending in `Georgia`, `WA`, or `CA` to the United States
  before the geocoder was asked, including `Tbilisi, Georgia`, `Perth, WA` (Western Australia),
  and `Toronto, CA`. `Georgia` and `WA` are now decided by the text rules only if a known US
  city or state precedes them, otherwise by the geocoder. `CA` remains California unless a
  Canadian city or province precedes it.
  `scripts/maintenance/recheck_ambiguous_locations.py` applies the corrected rules to stored
  rows (dry run unless `--write` is given).
- Errors while writing a page of repositories to the database were logged and ignored; the
  page counted as collected. They now end the collection, like a failed search.
- A call of the collection script resumed whatever state file it found, also a finished one
  and one written with another star range or time range; such a run processed only the last
  period of the earlier collection. The state file now records the mode and the star range
  and whether the collection has finished. A call with other arguments starts a new
  collection after a finished one and is refused while one is unfinished.
- A collection resumed with `--limit` ended the open period after one page, because the limit
  was compared with the running total of the state file instead of the current run.
- `repositories.organization_id` was not set when a repository was stored from a search result,
  because the search result carries no `organization` field. It is now derived from the type
  of the owner.
- A page size other than 100 (`GITHUB_PAGE_SIZE`) ended every period after the first page, and
  a result limit other than 1000 (`GITHUB_MAX_RESULTS`) made the split repeat without end.
- Network errors during the search are retried like API errors, and a search response without
  a total (for example after HTTP 404) is an error instead of an empty period.
- `update_location_geocoding.py`: `--retry-empty` no longer changes the cache when the run is
  only displayed (`--show-missing`) or declined; the cache is written at the end of every run;
  an error during the update sets exit code 1.
- The geocoding benchmark no longer counts requests the geocoder did not answer as correct or
  wrong.
- The test suite no longer writes into `logs/`.
- Geocoding no longer records an outage of the geocoder as "this location has no country".
  A timeout was cached without expiry and the row was marked as not geocodable, so the
  location, and every account using it, was never asked again. Unanswered requests are now
  retried once, are not cached, and leave the row open; the run exits with code 1 and stops
  after ten unanswered requests in a row.
- `update_location_geocoding.py --force` without `--limit` processed the same first batch
  indefinitely. Rows are now paged by id and each row is processed once per run.
- After a period with more than 1,000 search results had been split, the standard collection
  skipped the first of the resulting parts: the loop advanced to the next period although the
  pointer already stood on the first part. Every part is now collected.
- The star-range mode (`--star-range`) did not split such periods and stopped after 1,000
  results per period. It now splits them like the standard mode. The total is read from the
  first result page, so the check costs no additional request; the standard mode no longer
  issues a separate request for it.
- A failed repository search is no longer treated as an empty period. Previously any API
  error that outlasted the client's own retries, including an exhausted rate limit and invalid
  tokens, made the collector record zero repositories for the period, mark it as done and
  move on. The collection now stops after three attempts (immediately for invalid tokens),
  leaves the period open, and `collect_repositories.py` exits with code 1 and names the period;
  the same call resumes there.
- The test suite no longer modifies `data/collection_state.json`. Tests that ran the collection
  script used the real state file; every test now gets a temporary one.
- Six entry points (`collect_repositories.py`, `enrich_repository_stats.py`,
  `update_location_geocoding.py`, `export_tables_to_csv.py`, `cleanup_orphaned_owners.py`,
  `reset_and_collect.py`) started only when the package was installed. They now add `src/` to
  the import path themselves, like the other scripts.
- Comments and docstrings no longer point to files that do not exist or that are not part of
  the repository.
- `scripts/enrichment/enrich_org_data_graphql.py` no longer writes its own timestamp and run identifier
  to `organizations.orbis_enriched_at` and `orbis_run_id`; it records the fetch in
  `organizations.enrichment_checked_at`. Organizations that GitHub no longer knows are recorded
  as fetched instead of being requested again on every run, and a failed request no longer
  counts as "not found". **The migration has to be applied before the script is used.**
- The orphan cleanup (`scripts/maintenance/cleanup_orphaned_owners.py`,
  `collect_repositories.py --cleanup-owners`) no longer deletes contributors that are
  referenced in `repository_contributors`. It now also finds organizations without a
  repository; previously it found none as soon as the database contained a repository owned
  by a user account.

### Removed

- `src/github_collector/cli/collect_command.py` and `cli/reset_command.py`, an older second
  collection and reset interface that no script used; the collection is run by
  `scripts/collection/collect_repositories.py`.
- `scripts/enrich_cctld_contributors.py`, a near-identical copy of `scripts/enrichment/enrich_cctld.py`;
  use `scripts/enrichment/enrich_cctld.py --entity contributors` instead.
- `scripts/enrich_contributor_data_graphql.py`, superseded by
  `scripts/enrichment/enrich_contributor_profiles.py`. The removed script selected contributors by empty
  profile fields and therefore requested genuinely empty profiles again on every run.
- One-off diagnostic, validation, and migration scripts that are no longer referenced, and the
  SQL files of the superseded Oracle-side matching pipeline.
- The workstreams `alt_source_bias_check` (a side study comparing other platforms) and
  `patstat_orbis_bvdid_enrichment` (a one-off task for a different project). Neither is part of
  the collector.
- `archive/` (superseded prototypes of the Orbis matching pipeline).
- Data files that do not belong in version control: CSV exports of collected GitHub profiles,
  manual validation samples containing Orbis matches, and personal transfer files.

## 2.2.0

Baseline for this changelog.
