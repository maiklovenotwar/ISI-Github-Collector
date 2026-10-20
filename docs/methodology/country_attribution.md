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

`scripts/diagnostics/methodology_checks.sql` (query 5) compares the two
rows of each organization. In the project database on 2026-10-16, 735,205
organizations had both rows. For 118,490 of them only the row in
`organizations` carried a country, for 398 only the placeholder row, and
for 64 the two rows carried different countries.

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
`repositories.organization_id`. Versions before 2026-10-13 did not set
that column when a repository was stored from a search result. In the
project database on 2026-10-16, 1,271,265 of the 2,951,091 repositories
owned by an organization carried it (query 6 of
`scripts/diagnostics/methodology_checks.sql`). For 15 repositories the
owner's account has the type `Organization` but no row in
`organizations`, and 11 repositories owned by a user carry an
`organization_id`; both are probably accounts whose type changed between
retrievals.

By this rule, 12,558,621 of the 24,547,404 repositories owned by a user
(51.2%) and 1,263,438 of the 2,951,091 repositories owned by an
organization (42.8%) had a country on that day (on 2026-10-13, before the
corrections described in `geocoding.md`, 51.6% and 43.7%).

A repository whose owner has no country has no country. The CSV export
writes each table separately and does not apply this rule; it has
to be applied in the analysis.

## Three Sources of a Country

| Column | Source | Available for | Described in |
|--------|--------|---------------|--------------|
| `country_code` | Free-text location of the profile, resolved by geocoding | persons and organizations | `geocoding.md` |
| `country_code_cctld` | Country-code top-level domain of the website or e-mail address | persons and organizations | this document, below |
| `country_code_orbis` | Country of the Orbis entity matched to the organization | organizations | `orbis_enrichment.md` |

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

The project database illustrates the shift. On 2026-10-16 the United
States led the organizations with a geocoded country by a wide margin
(56,476, followed by Germany with 15,564), but did not appear among the
fifteen most frequent countries assigned by the ccTLD step, neither for
organizations (led by Germany with 1,087) nor for the accounts in
`contributors` (led by China with 9,087).

Several country-code domains are used mainly by services or for word play
rather than by holders in that country. A measurement on 2026-10-14 found
that `linktr.ee` accounted for 2,173 of the 2,373 accounts assigned to
Estonia (`EE`), `notion.so` for 165 of the 313 accounts assigned to
Somalia (`SO`), and `youtu.be` for 135 of the 867 accounts assigned to
Belgium (`BE`). Free or very cheap endings (`.tk`, `.ml`, `.ga`, `.cf`,
`.gq`, `.pw`) and endings used mostly for word play (`.re`, `.pm`, `.tf`,
`.st`, `.cx`, `.ax`, `.gs`, `.yt`, `.wf`, `.tl`, `.lc`, `.ag`, e.g. `frida.re`,
`hex.pm`; `.ag` is mostly read as *Aktiengesellschaft* or *agency*) showed the same pattern. In total,
an estimated 4,000 to 5,000 of about 96,000 assignments (about 5%) were affected.
The procedure therefore yields no country for these endings, nor for the
service domains `linktr.ee`, `buymeacoff.ee`, `coff.ee`, `lin.ee`,
`notion.so`, and `youtu.be`, the link shorteners `rb.gy`, `short.gy`, and
`surl.li`, the hosting services `sr.ht`, `self.so`, `free.nf`, and `co.nf`,
and their subdomains (a different domain that
merely ends in the same letters, such as `mynotion.so`, still counts).
Two endings remain in use but are uncertain: `.rs` (Serbia) is frequent
among Rust projects (`serde.rs`), and `.is` (Iceland) is also used for
word play. Results for these two countries, and for small countries in
general, should be read with caution.

Two further limits apply to this source.

- A country-code domain states where a domain is registered, not where
  its holder is located.
- Before writing, the script compares its result with the geocoded
  country for all rows that have both, and stops if fewer than 80% agree.
  On 2026-10-16 the agreement was 95.6% for organizations (47,199 rows
  with both values) and 89.3% for accounts in `contributors` (242,080
  rows); in a dry run on 2026-10-09, before service domains were excluded
  and the geocoding was corrected, it was 91.8% and 84.4%. This is the agreement of two estimates, not the
  accuracy of either: both can be wrong in the same way. The overall rate
  can also hide countries for which almost every assignment is wrong; the
  validation therefore also reports the fifteen countries with the lowest
  agreement among those with at least 20 evaluated rows.

## Orbis

`country_code_orbis` is the result of matching organizations to companies
in a commercial database. It is the country of the matched entity, which is
not necessarily the headquarters, and for some cases agrees with the geocoded
country by construction. It is not part of the results of the collection
pipeline and is not used to fill missing countries; it serves as context or
for sensitivity analyses once a validation round has met its acceptance
criterion (`manual_validation.md`). See `orbis_enrichment.md`.
