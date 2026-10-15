# Contributor Network

Description of how the per-repository contributor list in the table
`repository_contributors` is collected, how accounts are identified, and
which limits apply when the data are interpreted. See
`docs/architecture/database.md` for the column definitions.

## Purpose

`repositories.owner_id` records exactly one account per repository: its
owner. It does not record who contributed to a repository. The contributor
network adds this relation: one row per repository and contributing
account, with the number of contributions reported by GitHub. Together
with the country code of each account, it supports repository-level
statements such as the country composition of the contributors of a
repository.

## Procedure

The collection runs in four steps for one range of star counts.
`scripts/collection/collect_contributor_network.py` starts them in this
order; each step is a separate script and can be run on its own.

| Step | Script | Source | Writes |
|------|--------|--------|--------|
| 1 | `scripts/collection/collect_repository_contributors.py` | GitHub REST API, `GET /repos/{owner}/{repo}/contributors` with `anon=true` | `repository_contributors` (one row per repository and login) |
| 2 | `scripts/collection/add_missing_contributors.py` | `repository_contributors` (no API request) | `contributors`: a row with `id`, `login`, and `type` for each account not yet present; `repository_contributors.contributor_id` |
| 3 | `scripts/enrichment/enrich_contributor_profiles.py --scope new` | GitHub GraphQL API, user profiles | profile columns of `contributors`, `enrichment_checked_at` |
| 4 | `scripts/enrichment/update_location_geocoding.py --contributors` | geocoding service | `contributors.country_code`, `region` |

Step 1 selects the repositories whose star count lies in the half-open
range from `--min-stars` (inclusive) to `--max-stars` (exclusive) and that
have no row in `repository_contributors` yet. A repository with rows is
therefore not requested again unless `--force` is given. A repository
without rows is requested again in every run that covers its star count;
this applies to repositories whose request failed and to repositories for
which GitHub returned an empty list. Rows are written with an
upsert on the unique key (`repository_id`, `github_login`), so a repeated
run updates existing rows instead of duplicating them. Every row carries
the identifier of the run that wrote it (`run_id`), which allows the rows
of a single run to be removed.

Step 3 starts one process per configured GitHub token. The accounts are
divided among the processes by their numeric identifier, so that no
account is requested twice.

## Identification of Accounts

Accounts are matched by the numeric GitHub user identifier
(`repository_contributors.github_user_id`, equal to `contributors.id`),
not by the login. The identifier remains stable when an account is
renamed; the login does not.

Three kinds of entries are distinguished:

- **Accounts.** The API returns login, identifier, and account type. The
  row is linked to `contributors` through `contributor_id` once a row for
  the account exists (step 2).
- **Anonymous authors.** Commits whose author e-mail address is not
  associated with a GitHub account are returned without login and
  identifier. They are stored with `is_anonymous` set, without
  `github_user_id`, and with the substitute login `anon:<e-mail or name>`.
  They cannot be linked to an account and carry no country information.
- **Bots.** Accounts of type `Bot` are stored in `repository_contributors`
  but receive no row in `contributors` in step 2, and consequently no
  profile and no country code.

## Limits

- **Only the first 500 author e-mail addresses are linked to accounts.**
  GitHub identifies contributors by the e-mail address of the commit
  author, and states that "only the first 500 author email addresses in
  the repository link to GitHub users. The rest will appear as anonymous
  contributors without associated GitHub user information." In a large
  repository, contributors beyond that limit are therefore stored as
  anonymous although they have an account. A read-only count on
  2026-10-14 found no repository with 500 or more linked accounts; the
  maximum was 490, and 190 repositories had 450 or more. The 4,131,585
  anonymous rows (30% of all rows) partly result from this limit.
  Statements about the country composition of large repositories are
  restricted accordingly.
- **At most 2,000 entries per repository.** Step 1 requests up to 20
  pages of 100 entries (`--max-pages`). GitHub returns contributors in
  descending order of contributions, so the entries with the fewest
  contributions are missing. On 2026-10-14 three repositories reached this
  limit.
- **Repositories without a contributor list.** For some very large
  repositories GitHub does not return the list (HTTP 403, history or
  contributor list too large). These repositories have no rows. The script
  writes the repositories that failed in a run to
  `failed_repos_<run>.txt` in the log directory.
- **Contributions are commit counts** as reported by the endpoint
  ("number of commits per contributor"); GitHub does not state on which
  branches they are counted. Other forms of contribution (issues,
  reviews, discussions) are not included.
- **Anonymous authors are counted by e-mail address.** A person who
  committed under several addresses appears several times.
- **Bots are recognised by account type only.** 195,412 rows have the
  type `Bot`. Automated accounts registered as ordinary users are not
  recognised: 53,726 rows of type `User` contain "bot" in the login, a
  rough upper bound that also includes names such as `abbott`.
- **Deleted and empty repositories look alike.** A repository that no
  longer exists (HTTP 404) or has no commits (HTTP 204) yields an empty
  list. Neither is recorded as failed, and both are requested again in
  every run that covers their star count.
- **Country information is self-reported.** The country code is derived
  from the free-text `location` field of the profile. Accounts without a
  location, or with a location that cannot be resolved, have no country
  code.
- **A single point in time.** The rows of a repository reflect the request
  in which they were written (`collected_at`); the table does not record
  when a contribution was made.
- **Database.** Steps 1 and 2 write with MySQL-specific statements. On
  other databases they run only with `--dry-run`.

## Coverage

State of the production database on 2026-08-29, after three collection
runs (repositories with at least 5,000 stars, then 1,000 to 4,999, then
500 to 999):

| Measure | ≥ 5,000 stars | ≥ 1,000 stars | ≥ 500 stars |
|---|---:|---:|---:|
| Repositories with a contributor list / repositories in range | 10,230 / 10,278 | 55,182 / 55,493 | 105,809 / 106,527 |
| Rows in `repository_contributors` | 1,863,548 | 4,102,819 | 5,356,175 |
| Rows linked to an account in `contributors` | 62.1% | 67.6% | 68.9% |
| Rows whose account has a country code | 39.3% | 42.2% | 40.9% |

The columns are cumulative. Roughly three in five rows therefore carry no
country code; statements about the country composition of a repository
refer to the located part of its contributors only.

## Not Covered

The contributor network describes who writes code. It does not describe
who uses a repository. Forks, stargazers, dependency relations, and
commit time series are not collected.
