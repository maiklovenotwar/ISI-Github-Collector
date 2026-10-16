# Repository Collection

Description of which repositories the collector records, how it retrieves
them, and what can and cannot be said about the completeness of the
result. The procedure is implemented in
`src/github_collector/repository_collector.py`; the entry point is
`scripts/collection/collect_repositories.py` (see `docs/usage.md`).

## Population

The collector records **public repositories that are not forks**, that
were **created in a chosen time range**, and that had **at least a chosen
number of stars at the time of retrieval**.

The population is defined by the query sent to the GitHub repository
search:

```
created:<start>..<end> stars:>=<minimum>
created:<start>..<end> stars:<minimum>..<maximum>      (star-range mode)
```

Three properties follow from this query.

- **Forks are excluded.** The GitHub search returns forks only when the
  query contains `fork:true` or `fork:only`. The query contains neither.
  The column `repositories.fork` is therefore `False` for every collected
  repository; it does not indicate a missing value. The exclusion is a
  deliberate delimitation: a fork is a copy of a repository under another
  account, and most forks carry no development of their own. Its size can
  be estimated with `scripts/diagnostics/count_excluded_forks.py`, which
  reports how many forks and how many non-fork repositories the search
  announces above a star threshold. On 2026-10-13 the search announced:

  | Stars at least | Forks | Non-fork repositories | Forks per 100 non-forks |
  |---:|---:|---:|---:|
  | 1 | 7,095,401 | 32,534,721 | 21.8 |
  | 10 | 111,525 | 2,902,023 | 3.8 |
  | 100 | 10,104 | 483,785 | 2.1 |
  | 1,000 | 773 | 65,551 | 1.2 |

  These totals are approximations reported by GitHub. The exclusion is
  therefore substantial at a threshold of one star and small at higher
  thresholds.
- **Private and deleted repositories are excluded.** The search covers
  public repositories that exist at the time of the request.
- **Membership depends on the star count at retrieval.** A repository
  that reaches the minimum number of stars after its creation period was
  retrieved is not part of the collection.

## Procedure

1. The time range is divided into periods (30 days by default).
2. For each period the search is requested page by page, 100 results per
   page, sorted by stars in descending order.
3. The GitHub search returns at most 1,000 results per query. The first
   page of each period states the total number of matches. If it exceeds
   1,000, the period is divided into equal parts (number of matches
   divided by 1,000, plus one), and each part is processed in the same
   way, including a further division if necessary. Periods are closed
   intervals, so two adjacent parts share their boundary instant; a
   repository retrieved twice is stored once, because rows are written by
   repository identifier.
4. For each repository the collector stores the descriptive fields, the
   star count and the timestamps returned by the search, and the owner.
   The owner is stored as a row in `contributors`; an owner of type
   `Organization` is additionally stored in `organizations` (see
   `country_attribution.md`).
5. Progress is saved after every page. An interrupted collection resumes
   at the period and page at which it stopped.

Search responses are cached locally for 24 hours. A repeated request
within that time returns the stored response, including its star counts.

An owner whose account is already in the database is not retrieved again
when a further repository of that owner is collected. The profile of an
owner, including its location, is therefore as old as its first retrieval
or as the later profile enrichment, whichever is more recent. If the
retrieval of an owner fails, the owner is treated as known for the rest of
the run and the repository is stored without an owner row; on 2026-10-14
no repository was stored without one. Organizations are looked up by
login, so an organization that was renamed is not found under its new
name.

The profile enrichment of organizations
(`scripts/enrichment/enrich_org_data_graphql.py`) fills only organizations
whose name, website, e-mail address, location, creation date, and country
are all empty and that have not been checked before; it does not
overwrite existing values.

## Reference Date

The collection is not a snapshot of a single day. Each repository carries
the time of its retrieval (`collected_at`) and the star count at that time
(`stars_at_collection`). If a repository is retrieved again, both values
are overwritten; they describe the most recent retrieval. A collection
that runs for several weeks therefore combines repositories observed on
different days. Star counts, fork counts, and issue counts are states at
the time of retrieval; the collector does not record their history.

In the project database on 2026-10-13, the 27,498,495 repositories had
been retrieved between 2025-11-28 and 2026-01-18 (16.8 million in
December 2025). For 5,294,213 of them (19%) no time of retrieval is
recorded; they were stored before the column was introduced. Two
repositories were retrieved again on 2026-10-13.

Because membership depends on the star count at retrieval, repositories
created shortly before their retrieval are under-represented: they had
little time to gather stars. In the project database (2026-10-16), 239,748
repositories were created in December 2025, against about 310,000 per
month earlier in that year. Comparisons over time should end some months
before the retrieval.

## Completeness

The collector takes the following measures against silent loss.

- **Errors stop the collection.** A search request is attempted up to
  three times, also after a network error. If it still fails, the
  collection ends with an error, the period remains open, and the script
  exits with code 1. Invalid tokens end the collection at once. The same
  applies when the retrieved repositories cannot be written to the
  database. The same call resumes at the open period.
- **The state file belongs to one collection.** It records the mode and
  the star range of the collection it describes and whether that
  collection has finished. A call with other arguments starts a new
  collection only if the recorded one has finished; otherwise it is
  refused.
- **Oversized periods are divided** in both collection modes, as
  described above.
- **Each finished period is recorded** in `logs/collection_audit.jsonl`
  with the number of matches GitHub announced, the number retrieved, the
  number written to the database, and the difference between announced
  and retrieved. `scripts/diagnostics/summarize_collection_audit.py` lists
  the periods in which fewer repositories arrived than announced or fewer
  were stored than retrieved. The retrieved number counts search results;
  a repository returned twice within a period is counted twice.

Three limits remain.

- **The announced total is approximate.** GitHub documents the total
  number of matches of a search as an approximation. A small difference
  between the announced and the collected number is therefore not proof
  of a loss, and agreement is not proof of completeness.
- **GitHub may return an incomplete answer.** If a search exceeds
  GitHub's time limit, the response contains the matches found so far and
  is marked `incomplete_results`. The collector logs such responses and
  records them in the collection state; it cannot recover the missing
  matches.
- **A period of one second cannot be divided further.** GitHub resolves
  creation times to the second. If more than 1,000 matching repositories
  were created within the same second, the first 1,000 by star count are
  collected and the period is recorded as truncated.

### Collections Made Before 2026-10-13

Six defects affected earlier versions of the collector. They are
described here because data collected with those versions may be
incomplete.

1. **Failed searches were treated as empty periods.** An error that
   outlasted the retries of the API client, including an exhausted rate
   limit and invalid tokens, made the collector record zero repositories
   for the period, mark it as finished, and continue. The period was not
   retrieved again later.
2. **The first part of a divided period was skipped** in the standard
   mode. After a period with more than 1,000 matches had been divided,
   the collection continued with the second part.
3. **The star-range mode did not divide periods.** It stopped after 1,000
   results per period; the repositories with the fewest stars in that
   period were missing.
4. **Errors while writing to the database were logged and ignored.** The
   page counted as collected although nothing had been stored.
5. **A finished state file was resumed by the next call**, whatever its
   arguments. A second collection with another star range or time range,
   started with the state file of the first, processed only the last
   period of the first.
6. **A collection resumed with `--limit` ended the open period after one
   page**, so that the remaining pages of that period were never
   requested.

In addition, `repositories.organization_id` was not set when a repository
was stored from a search result; see `country_attribution.md`.

Affected collections can be recognised by periods with markedly fewer
repositories than their neighbours. `scripts/diagnostics/methodology_checks.sql`
contains a query that counts the collected repositories per month of
creation.

In the project database on 2026-10-13 this count showed no such period:
every month from January 2008 to December 2025 is present, and no month
lies markedly below both of its neighbours. It shows one break. Among
the repositories created from January to July 2012, about 51,300 per
month have fewer than five stars; among those created from August to
December 2012, about 17,900 per month. The number of repositories with
at least ten stars does not change (about 5,300 and 5,500 per month).
The break coincides with the introduction of stars on GitHub in August
2012, when the earlier "watching" of a repository, which included the
creator's own, was converted into stars. This explanation has not been
verified against a second source. Star counts of one or a few stars are
therefore not comparable between repositories created before and after
that month.

## Fields Not Collected

- `repositories.contributors_count` is not collected and is `NULL`.
  Until 2026-10-14, `scripts/enrichment/enrich_repository_stats.py` wrote
  0 into this column; on that day all 27,465,002 repositories with
  statistics carried 0, which would read as "no contributors". The
  contributors of a repository are recorded separately, see
  `contributor_network.md`.
- `commits_count` and `pull_requests_count` are filled only by that
  enrichment step and are `NULL` before it has run. `pull_requests_count`
  counts open, closed, and merged pull requests together;
  `commits_count` counts the commits on the default branch only. The time
  at which the counts were retrieved is not stored. On 2026-10-14,
  27,465,002 of 27,498,495 repositories (99.9%) had both counts. For the
  other 33,493 GitHub returned no data; the GraphQL API does this when no
  repository is found under the stored owner and name, for example after
  a deletion, a renaming, or a change to private. The causes were not
  counted separately. They are listed in the
  file of failed identifiers and are selected again in every run, because
  no column records that a request was made.

## Outlook: Relations Between Repositories and Accounts

The collection describes repositories and their owners. It does not
describe how repositories are used by others. Three relations are
available from GitHub and would extend the data in that direction.

- **Contributors** of a repository are collected for repositories above
  a star threshold; see `contributor_network.md`.
- **Forks** as a relation (which account copied which repository, and
  when) indicate use and further development. They would be collected per
  repository from `GET /repos/{owner}/{repo}/forks` into a table of their
  own, in the same way as the contributor lists. This is distinct from
  including forks as repositories in the population, which is not
  intended.
- **Stargazers** would indicate interest in a repository. They are not
  collected.
