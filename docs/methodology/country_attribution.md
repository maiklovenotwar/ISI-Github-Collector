# Country Attribution

Rules for assigning a country to an account and to a repository, and for
counting accounts. The database stores several country estimates side by
side and stores organizations in two tables; this document states which
values are used for analysis.

## Unit of Analysis

The table `contributors` holds one row per GitHub account, irrespective of
its role, and `organizations` holds one row per organization that owns a
collected repository. An organization that owns a repository therefore has
two rows: one in `organizations` and one in `contributors` with
`type = 'Organization'`. The second row exists because every repository
refers to its owner through `repositories.owner_id`, which points to
`contributors`. Both rows carry the same GitHub account identifier. They
are enriched and geocoded independently and can differ.

The following rules avoid double counting.

- **Organizations** are counted and analysed through the table
  `organizations`.
- **Persons** are the rows of `contributors` with `type = 'User'`.
- Rows of `contributors` with `type = 'Organization'` are placeholders.
  They are not counted, and their country is not used.
- Rows of `contributors` with `type = 'Bot'` are not counted as persons.
- `contributors` contains owners of repositories and, since the
  contributor network was collected, accounts that contributed to a
  repository without owning one (see `contributor_network.md`). A
  statement about "contributors" has to say which of the two groups it
  refers to.

`scripts/diagnostics/methodology_checks.sql` (query 7) counts how many
organizations carry different countries in their two rows.

## Country of a Repository

A repository has no location of its own. Its country is the country of
its owner:

- if the owner is an organization (the owning row in `contributors` has
  `type = 'Organization'`), the country of the row with the same
  identifier in `organizations`;
- otherwise the country of the owning account in `contributors`.

```sql
SELECT r.id,
       CASE WHEN c.type = 'Organization' THEN o.country_code
            ELSE c.country_code END AS country_code
FROM repositories r
LEFT JOIN contributors c ON c.id = r.owner_id
LEFT JOIN organizations o ON o.id = r.owner_id;
```

The rule uses the account type of the owner, not the column
`repositories.organization_id`. Collections made before 2026-10-13 left
that column empty for repositories found through the search; it is set by
later collections. Query 12 of `scripts/diagnostics/methodology_checks.sql`
shows how many repositories carry it.

A repository whose owner has no country has no country. The CSV export
writes the three tables separately and does not apply this rule; it has
to be applied in the analysis.

## Three Sources of a Country

| Column | Source | Available for | Described in |
|--------|--------|---------------|--------------|
| `country_code` | Free-text location of the profile, resolved by geocoding | persons and organizations | `geocoding.md` |
| `country_code_cctld` | Country-code top-level domain of the website or e-mail address | persons and organizations | `orbis_enrichment.md`, section 5 |
| `country_code_orbis` | Headquarters of the matched company in the Orbis database | organizations | `orbis_enrichment.md` |

No script overwrites the column of another source.

**Results are based on `country_code` alone.** It is the only source that
is obtained in the same way for persons and organizations and that can
yield any country. An empty string in `country_code` marks a location for
which the geocoder returned no match; it is treated like a missing value.

## ccTLD as a Sensitivity Analysis

The ccTLD step derives a country from domains such as `.de` or `.co.uk`
and is applied only to rows without a geocoded country. It is not used to
fill gaps in the main results, for a structural reason: **generic domains
yield no country.** Organizations and persons in the United States
predominantly use `.com`, `.org`, or `.io`, which the procedure rightly
discards. Filling missing countries from this source therefore adds
almost only countries other than the United States and shifts the
distribution towards countries whose own domain is in common use.

The effect of this source is reported separately: results are computed
once from `country_code` alone and once with `country_code_cctld` added
where `country_code` is missing, and the difference is stated.

Two further limits apply to this source.

- A country-code domain states where a domain is registered, not where
  its holder is located.
- Before writing, the script compares its result with the geocoded
  country for all rows that have both, and stops if fewer than 80% agree.
  In a dry run on 2026-10-09 the agreement was 91.8% for organizations
  (48,435 rows with both values) and 84.4% for accounts in `contributors`
  (254,219 rows). This is the agreement of two estimates, not the
  accuracy of either: both can be wrong in the same way.

## Orbis

`country_code_orbis` is the result of matching organizations to companies
in a commercial database. It is not part of the results of the collection
pipeline described here; see `orbis_enrichment.md`.
