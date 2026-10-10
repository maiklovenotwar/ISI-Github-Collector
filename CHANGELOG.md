# Changelog

Notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## Unreleased

### Added

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
