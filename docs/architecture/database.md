# Database Schema

## Overview

The application uses a SQLAlchemy-compatible database (SQLite or MySQL) to
store collected data. See the main `README.md` for how the backend and
connection string are configured (`DATABASE_URL`; no SQLite fallback).

The schema has five tables:

1. **contributors**: GitHub users
2. **organizations**: GitHub organizations
3. **orbis_enrichments**: per-run history of Orbis matching results for an
   organization, with rollback support
4. **repositories**: GitHub repositories
5. **repository_contributors**: per-repository contributor list, including
   each contributor's commit share

## Table Schemas

### contributors

| Column | Type | Description |
|--------|------|-------------|
| id | Integer | Primary key; GitHub user ID |
| login | String, unique, not null | GitHub username |
| name | String | Display name |
| email | String | Email address |
| type | String | Account type as reported by the GitHub API |
| avatar_url | String | Avatar URL |
| company | String | Company field from the GitHub profile |
| blog | String | Blog/website URL |
| location | String | Free-text location from the GitHub profile |
| country_code | String(2), indexed | ISO country code (from geocoding) |
| region | String | Region within the country/continent (from geocoding) |
| bio | Text | Biography |
| twitter_username | String | Twitter/X username |
| public_repos | Integer | Number of public repositories |
| public_gists | Integer | Number of public gists |
| followers | Integer | Follower count |
| following | Integer | Following count |
| created_at | DateTime | GitHub account creation date |
| updated_at | DateTime | GitHub account last-update date |
| country_code_cctld | String(2) | ISO country code derived from the country-code top-level domain of `blog` or `email` (see `scripts/enrichment/enrich_cctld.py`) |
| cctld_source | String(20) | Field from which the code was derived: `blog`, `email`, or `both` |
| cctld_enriched_at | DateTime | Timestamp of the ccTLD enrichment |
| cctld_run_id | String(36) | UUID of the ccTLD enrichment run; used to revert a run |
| enrichment_checked_at | DateTime, indexed | Timestamp of the last profile-enrichment attempt (set even when the result was empty — distinguishes "never attempted" from "attempted, nothing found") |

### organizations

| Column | Type | Description |
|--------|------|-------------|
| id | Integer | Primary key; GitHub organization ID |
| login | String, unique, not null | GitHub login |
| name | String | Display name |
| email | String | Email address |
| type | String | Account type as reported by the GitHub API |
| avatar_url | String | Avatar URL |
| company | String | Company field from the GitHub profile |
| blog | String | Blog/website URL |
| location | String | Free-text location from the GitHub profile |
| country_code | String(2), indexed | ISO country code (from geocoding) |
| region | String | Region within the country/continent (from geocoding) |
| bio | Text | Biography |
| twitter_username | String | Twitter/X username |
| public_repos | Integer | Number of public repositories |
| public_gists | Integer | Number of public gists |
| followers | Integer | Follower count |
| following | Integer | Following count |
| public_members | Integer | Number of public organization members |
| created_at | DateTime | GitHub account creation date |
| updated_at | DateTime | GitHub account last-update date |
| bvd_id | String, indexed | Orbis BvD identifier of the current match |
| country_code_orbis | String(2) | ISO country code from Orbis (ground truth) |
| country_code_cctld | String(2) | ISO country code derived from the country-code top-level domain of `blog` or `email` (see `scripts/enrichment/enrich_cctld.py`) |
| cctld_source | String(20) | Field from which the code was derived: `blog`, `email`, or `both` |
| cctld_enriched_at | DateTime | Timestamp of the ccTLD enrichment |
| cctld_run_id | String(36), indexed | UUID of the ccTLD enrichment run; used to revert a run |
| enrichment_checked_at | DateTime | Timestamp of the last profile fetch by `scripts/enrichment/enrich_org_data_graphql.py` (set even when GitHub no longer knows the organization — distinguishes "never attempted" from "attempted, nothing found") |
| orbis_match_type | String(20) | Legacy column. Not written by the current code; the Match-Type of a match is stored in `orbis_enrichments.match_type` |
| orbis_enriched_at | DateTime | Timestamp of the current Orbis match; `NULL` for organizations without a match |
| orbis_run_id | String(36), indexed | Legacy column, no longer written. The run identifier of an Orbis match is stored in `orbis_enrichments.run_id`. Existing values stem from earlier runs of `scripts/enrichment/enrich_org_data_graphql.py`, which stored its own run identifier here until the migration `add_organizations_enrichment_checked_at.sql` |
| current_orbis_enrichment_id | Integer, FK → orbis_enrichments.id (`SET NULL` on delete) | Points at the active row in `orbis_enrichments`; `NULL` after a rollback. The referenced row itself is kept on rollback (see `orbis_enrichments.reverted_at`), so a match remains auditable even once reverted. |

### orbis_enrichments

Per-run history of Orbis matching results, decoupled from the three
result columns on `organizations` (`bvd_id`, `country_code_orbis`,
`orbis_enriched_at`) so that individual runs can be inspected and rolled
back independently.

| Column | Type | Description |
|--------|------|-------------|
| id | Integer | Primary key |
| organization_id | Integer, FK → organizations.id (`CASCADE` on delete), not null, indexed | Organization this row belongs to |
| bvd_id | String, not null, indexed | Matched Orbis BvD identifier |
| company_name | String | Company name at match time (Orbis `NAME_NATIVE`) |
| company_country | String(2) | Company country at match time (Orbis `CTRYISO`) |
| company_city | String | Company city at match time (Orbis `CITY_NATIVE`) |
| company_website | String | Company website at match time (Orbis `WEBSITE`) |
| match_type | String, not null, indexed | Match-Type (`combined_high`, `combined_low`, ...; see `CONTEXT.md`) |
| hq_method | String | Headquarters-resolution signal that was used (`ROOT_DOMAIN_PRESENCE`, `BVDID_FALLBACK`, ...; see `docs/methodology/orbis_enrichment.md`) |
| score | Integer, not null, indexed | Matching score |
| blog_matched | Boolean, not null | Whether the match was corroborated by a blog-domain match |
| run_id | String, not null, indexed | UUID of the enrichment run |
| enriched_at | DateTime, not null | Timestamp of this match attempt |
| reverted_at | DateTime, nullable | Set when this run was rolled back; `NULL` while the match is active |
| created_at | DateTime | Row creation timestamp |

Unique constraint on (`organization_id`, `run_id`).

A row is never deleted on rollback — only `reverted_at` is set — so that
there remains a record of which GitHub organization was linked to which
Orbis company at the time, even after the link is undone. Company data
(name/country/city/website) is stored directly on each row rather than in a
separate company-lookup table, since this is a log of match *attempts*
(including reverted ones), not a cache of current company facts; a given
company's recorded details may therefore legitimately differ between rows if
Orbis data changed between runs.

### repositories

| Column | Type | Description |
|--------|------|-------------|
| id | Integer | Primary key; GitHub repository ID |
| name | String, not null | Repository name |
| full_name | String, unique, not null | `owner/name` |
| owner_id | Integer, FK → contributors.id, not null, indexed | Repository owner (always a contributor row, even for org-owned repos — see `organization_id`) |
| organization_id | Integer, FK → organizations.id, indexed | Set if the repository is owned by an organization |
| description | Text (`LONGTEXT` on MySQL) | Repository description |
| homepage | String | Homepage URL |
| language | String, indexed | Primary language |
| private | Boolean | Whether the repository is private |
| fork | Boolean | Whether the repository is a fork |
| default_branch | String | Default branch name |
| size | Integer | Repository size in KB |
| stargazers_count | Integer, indexed | Star count |
| watchers_count | Integer | Watcher count |
| forks_count | Integer | Fork count |
| open_issues_count | Integer | Open issue count |
| contributors_count | Integer | Not collected; `NULL` (until 2026-10-14 filled with 0, see `docs/methodology/repository_collection.md`) |
| commits_count | Integer | Number of commits on the default branch |
| pull_requests_count | Integer | Number of pull requests (open, closed, and merged) |
| created_at | DateTime | Repository creation date |
| updated_at | DateTime | Repository last-update date |
| pushed_at | DateTime | Last push date |
| collected_at | DateTime, indexed | Timestamp of this collector's crawl |
| stars_at_collection | Integer | Star count at crawl time |
| collection_status | String | `active`, `archived`, `deleted`, etc. |

### repository_contributors

Full per-repository contributor list (who contributed, and how much),
complementing the single owner relationship on `repositories`. Mirrors
`GET /repos/{owner}/{repo}/contributors`; the basis for repository-level
network indicators such as cross-border co-contribution. The collection
procedure and its limits are described in
`docs/methodology/contributor_network.md`.

| Column | Type | Description |
|--------|------|-------------|
| id | Integer | Primary key |
| repository_id | Integer, FK → repositories.id (`CASCADE` on delete), not null, indexed | Repository |
| contributor_id | Integer, FK → contributors.id (`SET NULL` on delete), nullable, indexed | Linked contributor row, if one exists |
| github_login | String, not null | GitHub login as reported by the contributors endpoint |
| github_user_id | Integer, indexed | Stable GitHub user ID (equals `contributors.id` when linked) |
| github_type | String | Account type reported by the API (`User`, `Bot`, ...) |
| contributions | Integer | Commit count on the default branch, as reported by the API |
| is_anonymous | Boolean, not null | True for anonymous (email-only, no linked account) contributors |
| collected_at | DateTime, not null | Timestamp of this crawl |
| run_id | String, indexed | UUID of the collection run (for revert) |

Unique constraint on (`repository_id`, `github_login`). `contributor_id` is
deliberately nullable: anonymous contributors and logins not yet present in
`contributors` are still recorded via `github_login` rather than discarded.

## Relationships

- A repository belongs to exactly one owner, either a contributor or an
  organization, via `owner_id` (always set) and `organization_id`
  (set only for organization-owned repositories).
- An organization has zero or more `orbis_enrichments` rows (its match
  history); `current_orbis_enrichment_id` points at the currently active one.
- A repository has zero or more `repository_contributors` rows.

## Indexes

Indexes exist on all foreign keys and on the columns most frequently used in
lookups or filters: `contributors.login`, `contributors.country_code`,
`contributors.enrichment_checked_at`, `organizations.login`,
`organizations.bvd_id`, `organizations.orbis_run_id`,
`repositories.language`, `repositories.stargazers_count`,
`repositories.collected_at`, `orbis_enrichments.bvd_id`,
`orbis_enrichments.match_type`, `orbis_enrichments.score`,
`orbis_enrichments.run_id`, `repository_contributors.github_user_id`,
`repository_contributors.run_id`.

## Data Integrity

1. Every table has a primary key.
2. Required columns (`login`, `name`, foreign keys where applicable) are
   `NOT NULL`.
3. `login` is `UNIQUE` on `contributors` and `organizations`; `full_name` is
   `UNIQUE` on `repositories`; `repository_contributors` has a unique
   constraint on (`repository_id`, `github_login`); `orbis_enrichments` has
   one on (`organization_id`, `run_id`).

## Database Access

Access goes through the `GitHubDatabase` class, an abstraction over
SQLAlchemy. Selected methods:

- `insert_contributor(contributor_data)` / `update_contributor(contributor, contributor_data)`
- `insert_organization(org_data)` / `update_organization(organization, org_data)`;
  both update methods set `country_code` and `region` to `NULL` when the new
  data change the location and carry no country code themselves
- `insert_repository(repo_data)` / `update_repository(repo, repo_data)`
- `get_contributors(limit=None)` / `get_all_contributor_logins()`
- `get_all_organization_logins()`
- `get_organizations_without_country_code(limit=None)` / `get_contributors_without_country_code(limit=None)`
- `check_repository_exists(repository_id)`

`GitHubDatabase` also exposes generic helpers backed by an internal model
registry:

- `iter_rows_for_export(table_name, limit=None, chunk_size=10_000)`: the rows
  of a table (`"contributors"`, `"organizations"`, `"repositories"`) as
  dictionaries, read in blocks ordered by `id`; used by the CSV export
- `get_data_for_export(table_name, limit=None)`: the same as ORM objects,
  loaded at once (suitable for small tables only)
- `get_entities_without_location(entity_type, limit=None)`: contributors or
  organizations with a location string but no country code; used by the
  geocoding CLI
- `delete_orphaned_owners(dry_run=False, session=None)`: removes
  contributors that neither own a repository nor are referenced in
  `repository_contributors`, and organizations without a repository (or, in
  dry-run mode, only reports what would be removed)

## Migrations

Schema changes are versioned as individual SQL scripts under
`scripts/migrations/` (e.g. `add_orbis_columns.sql`,
`add_repository_contributors_table.sql`). There is no Alembic setup and no
automatic revision tracking; a new migration is added as another script in
that directory and applied manually against the database.

The `cctld_*` columns were added to `organizations` by
`add_cctld_columns.sql` and to `contributors` by
`add_cctld_contributors_no_index.py`. The two migrations differ in two
respects: only `organizations.cctld_run_id` is indexed, and
`cctld_source` is declared as `VARCHAR(20)` for organizations but as
`VARCHAR(10)` for contributors, whereas `models.py` declares `String(20)`
for both tables. The stored values (`blog`, `email`, `both`) fit either
length.

## Write Paths of the Enrichment Scripts

Each enrichment step is an independent script that updates a defined set
of columns. The following table lists, for every script, the rows it
selects and the columns it writes.

| Script | Table | Rows selected | Columns written |
|--------|-------|---------------|-----------------|
| `collect_repository_contributors.py` | `repository_contributors` | Repositories in the requested star range without a row in `repository_contributors` (all in range with `--force`) | All columns; `contributor_id` only where the account already exists in `contributors` |
| `add_missing_contributors.py` | `contributors`, `repository_contributors` | Accounts in `repository_contributors` that are not anonymous, not of type `Bot`, and absent from `contributors` | `contributors.id`, `login`, `type` (new rows); `repository_contributors.contributor_id` |
| `enrich_repository_stats.py` | `repositories` | `commits_count` or `pull_requests_count` `NULL` | `commits_count`, `pull_requests_count`, after each batch; `contributors_count` is not written |
| `enrich_org_data_graphql.py` | `organizations` | `name`, `blog`, `email`, `location`, `created_at`, and `country_code` all `NULL`, and `enrichment_checked_at` `NULL` | `name`, `email`, `blog`, `location`, `bio`, `created_at`, `updated_at` (each only if previously `NULL`); `enrichment_checked_at` (also for organizations GitHub no longer knows) |
| `enrich_contributor_profiles.py` | `contributors` | `enrichment_checked_at` `NULL`; restricted by `--scope` to contributors referenced in `repository_contributors` (`new`), all others (`gap`), or both (`all`, default) | `login`, `name`, `email`, `blog`, `location`, `bio`, `company`, `twitter_username`, `public_repos`, `public_gists`, `followers`, `following`, `created_at`, `updated_at` (overwritten with the values reported by GitHub), `enrichment_checked_at`; `country_code` and `region` are set to `NULL` where the location changed; for accounts GitHub no longer knows only `enrichment_checked_at` |
| `update_location_geocoding.py` | `contributors`, `organizations` | Rows with a `location` and no `country_code` (all rows with `--force`) | `country_code`, `region` |
| `enrich_cctld.py` | `organizations` (default) or `contributors` (`--entity contributors`) | `country_code` and `country_code_cctld` empty and a `blog` or `email` present; for organizations additionally `country_code_orbis` empty. With `--recheck`: all rows with `country_code_cctld` set | `country_code_cctld`, `cctld_source`, `cctld_enriched_at`, `cctld_run_id` (with `--recheck` only where the result differs) |
| `reset_stale_countries.py` | `contributors`, `organizations` | Rows with a country whose location the mapping table or the table of corrections assigns to another country or to none, the bare location `Georgia`, a territory with its own ISO code, a country from a text rule that has since changed, or a location that was never geocoded | `country_code`, `region` set to `NULL` (after a CSV backup) |
| `orbis_matching_pipeline.py` | `organizations`, `orbis_enrichments` | `country_code_orbis` `NULL` and a non-empty `name` or `login`; alternatively an explicit list of logins | `organizations.bvd_id`, `country_code_orbis`, `orbis_enriched_at`, `current_orbis_enrichment_id`; one row per match in `orbis_enrichments` |

Three properties of these write paths are relevant when interpreting the
data:

1. **Country codes are stored per source.** Geocoding writes
   `country_code`, the ccTLD step writes `country_code_cctld`, and Orbis
   matching writes `country_code_orbis`. No script overwrites the column of
   another source; a consolidated country code has to be derived at query
   time.
2. **Profile fetches are recorded separately from their result.** Both
   profile-enrichment scripts set `enrichment_checked_at` on every row for
   which GitHub gave a definite answer, including accounts that no longer
   exist and profiles whose fields are empty. A row without a country code
   can therefore be attributed either to a profile that was never fetched
   (`enrichment_checked_at` `NULL`) or to a profile that carries no usable
   location. If a request fails or GitHub returns no data for an account,
   the column is left `NULL` and the row is selected again by the next
   run.
3. **The two profile-enrichment scripts differ in how they treat existing
   values.** `enrich_org_data_graphql.py` fills empty fields only.
   `enrich_contributor_profiles.py` overwrites the profile fields with the
   values reported by GitHub, including `login`, which follows renamed
   accounts. Because it selects only rows that were never fetched, this
   affects rows that already carry profile data only in exceptional cases.

## Initialization

The database is initialized automatically on first use. Initialization can
also be triggered manually via `init_db(db_url)`.

## Performance

- Indexes on frequently queried columns (see above).
- Batch processing for inserts and updates.
- Caching of frequently accessed data.
- Deduplication before insert.

## Backup

For SQLite, use the `.backup` command. Regular backups are recommended,
particularly before large collection runs or schema migrations.
