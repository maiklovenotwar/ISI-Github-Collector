# Orbis Enrichment Methodology

Description of how GitHub organizations are matched to companies in the
Orbis database (Bureau van Dijk) and how one Orbis entity is chosen when
several qualify. See `CONTEXT.md` for the terminology (Match-Status,
Match-Type, Candidate/Contender/Hit, Headquarters Resolution) and
`docs/usage.md` for the command line
(`scripts/enrichment/orbis_matching_pipeline.py`). The logic is implemented
in `src/github_collector/enrichment/orbis_matching_logic.py` and
`hq_detection.py`; weights and thresholds are in `config/orbis_matching.yaml`.

**Status.** The matching has not yet been run on the project database
(apart from two test runs of three organizations each, both rolled back).
No accuracy estimate exists for the current procedure. `country_code_orbis`
is not used in analyses until a validation round under
`docs/methodology/manual_validation.md` has met its acceptance criterion.

## 1. Role of the Orbis Country

`country_code_orbis` is the country of the matched Orbis entity (`CTRYISO`).
It is not necessarily the country of the company's headquarters:

- for a `unique` or `consensus` match it is the country of the entity that
  was found, which may be a subsidiary or a branch;
- for a resolved `pending` match the headquarters resolution (section 6)
  prefers candidates in the country of the GitHub organization, so that the
  Orbis country agrees with the geocoded country partly by construction.

Agreement between `country_code_orbis` and the geocoded `country_code`
therefore does not validate either. Results of the collection pipeline are
based on the geocoded country (`country_attribution.md`); the Orbis country
is a separate estimate for context and sensitivity analyses, not a means to
fill missing countries.

## 2. Search

1. **Domain.** For each organization with a blog URL, the domain and its root
   domain are taken from the URL; domains on the list
   `config/blog_blacklist.txt` (hosting and social-media services) are
   skipped; for company portals listed there as `root:` entries, a subdomain
   is not searched under the portal domain. All Orbis entities whose `WEBSITE` matches are candidates.
2. **Name fallback.** Only if the domain search yields no candidate at all,
   and the GitHub name (or, without a name, the login) has at least five
   characters, Orbis is searched for that exact string in `NAME_NATIVE`
   (case-insensitive). Company names in non-Latin script are never found
   this way, because the GitHub name is written in Latin script. Orbis also
   holds a Latin name for most such companies (section 3), but that column has
   no index for a case-insensitive search, and a search there is not
   implemented.
3. An organization with neither a usable domain nor a long enough name is
   not searched; the outcome list records it as `not_searchable`.

## 3. Name Similarity and Plausibility

Names are compared after reducing them to the characters a–z and 0–9. If
one normalised name contains the other, the similarity is
0.7 + 0.3 × (shorter length / longer length); otherwise it is the ratio of
`difflib.SequenceMatcher`. Each Orbis name is compared with the GitHub
display name and the login; the higher value counts.

An Orbis name without any Latin letter would reduce to nothing. Such a name
is also compared through its transliteration (`unidecode`), and the higher
value counts, under three restrictions: legal forms are removed from the
original name first (`有限公司`, `株式会社`, `주식회사`, `ООО`, `АО`, `ЕООД`,
`ΑΕ`, and others), the remaining core needs at least two characters and,
after transliteration, at least five letters, and the transliteration
counts towards a name match (below) only for candidates found by their
domain. Kanji in Japanese names are read in their Chinese pronunciation and
are compared poorly. Candidates matched this way are flagged and validated
separately.

For candidates found by their domain whose original name contains no Latin
letter, the Latin name that Orbis holds in a second name column is compared
as well, and the highest of the three values (original name, Latin name,
transliteration) counts. The Latin name is mostly a romanisation with an
English legal form ("Co., Ltd."), occasionally a literal translation. English
legal forms (`co., ltd.`, `company limited`, `limited liability company`,
`llc`, `ltd`, `limited`, `kk`, `k.k.`, `y.k.`, `inc`, `corp`, `corporation`,
`jsc`, `pjsc`, `ojsc`, `cjsc`) are removed from it first; unlike the
transliteration, it has no minimum length. The test whether the complete
login appears inside a longer company name, used to rank candidates, also
checks this Latin name. Names
that mix Latin letters with another script are compared only as they are.
Candidates matched through the Latin name are flagged and validated
separately (sections 2 and 7 of `manual_validation.md`); the switch
`plausibility.use_latin_name` in `config/orbis_matching.yaml` turns the
comparison off. The name of the column is read from the local environment
variable `ORBIS_LATIN_NAME_COLUMN`; without it, the comparison is not made.

- **Plausibility filter:** a candidate with a similarity below 0.5 is
  discarded before any scoring.
- **Name match:** a similarity of at least 0.75 counts as a name match and
  earns name points (the threshold is a constant in the pipeline script).

## 4. Scoring

The point values are a set heuristic; they have not been calibrated against
data. The reasoning behind the name points is that longer names collide by
chance less often than short ones.

| Component | Points | Condition |
|---|---:|---|
| Name | 40 / 30 / 20 / 10 | name match; GitHub name (or login) of ≥ 15 / ≥ 11 / ≥ 7 / fewer characters |
| Blog | 20 | the candidate was found through the organization's domain |
| Combination | 10 | name match and blog |
| Generic name | −15 | name match without blog, name shorter than five characters |
| Containment only | −15 | similarity from the containment rule, no blog, and the Orbis country does not equal the GitHub country |

A candidate is a hit with at least 30 points; candidates of the name or
login fallback need 10, because they cannot earn blog or combination
points. A further function for a country bonus
(`calculate_country_code_adjustment`) exists in the code but is not called.

Match-Types: `combined_high` (≥ 60), `combined_medium` (≥ 40),
`combined_low` (≥ 30) for candidates found by domain; `name_only` and
`login_fallback` for candidates of the name fallback, by the search term.

## 5. Match-Status

After the plausibility filter and the scoring, entities that are branches of
the same base BvD identifier (suffixes such as `-B0001`, `-1002`) are merged
into one. The Match-Status follows from the hits, in this order:

1. no hit → `no_match`;
2. all hits come from the name or login fallback and none contains the full
   login as a token → `pending`;
3. exactly one BvD identifier → `unique`;
4. all hits that carry a country carry the same one (hits without a country
   are ignored) → `consensus`;
5. otherwise → `pending`.

Among several hits the best candidate is chosen by score, then by a full
login token in the company name, then by the number of merged branches, the
similarity, and finally the BvD identifier.

## 6. Headquarters Resolution of `pending`

A `pending` organization is passed to the headquarters resolution, which
either chooses one candidate or leaves the organization without a result.
Every decision needs a corroboration: the candidate's Orbis country equals
the GitHub organization's geocoded country, or the candidate's website
equals the organization's root domain. Without a geocoded country (no
location, or a location without a match) only the domain counts. Candidates
without a corroboration are never chosen.

The signals are tried in a fixed order, each among the corroborated
candidates:

| Order | Signal | Data in the current pipeline |
|---:|---|---|
| 1 | `HQ_TABLE`: other entities name the candidate as their headquarters; most such subsidiaries wins | yes |
| 2 | `CONSOLIDATING_ENTITY`: consolidation code C1/C2 | no |
| 3 | `ROOT_DOMAIN_PRESENCE`: the only candidate whose website is the root domain (or the only one in the GitHub country among several) | yes |
| 4 | `OLDEST_FOUNDING`: unique oldest incorporation date | no |
| 5 | `HIGHEST_REVENUE`: unique highest operating revenue | no |
| 6 | `GUO_SELF`: the candidate is its own ultimate owner | no |
| 7 | `LARGE_CORP_HQ`: at least 1,000 employees, most wins | no |
| 8 | `IS_PARENT`: parent of other entities | no |
| 9 | `BVDID_FALLBACK`: alphabetically first BvD identifier among the corroborated candidates | — |

Signals 2 and 4 to 8 are implemented but receive no data, because the
pipeline does not query the Orbis tables they need; they never decide
today. Before the cascade, implausible names are filtered again and
branches are merged.

An organization that is `pending` only because all its hits come from the
name or login fallback (section 5) passes through the same cascade. Such
candidates have no website confirmation, so with a geocoded country the
cascade chooses among the candidates in that country, and a
namesake company in the same country can be chosen. These cases carry the
Match-Type `name_only` or `login_fallback` and are validated as a group of
their own (`manual_validation.md`, section 2).

The written row records the Match-Status (`pending`), the Match-Type of the
chosen candidate and the signal (`hq_method`). A further check of candidates
without an Orbis name found no effect (they cannot reach the minimum score),
so no change was made.

## 7. Validation

An informal review of an earlier version of the headquarters cascade (March
2026, about 50 cases) found a low precision. Its records are not available
and it cannot be reproduced; no figure from it is reported here. For the
current procedure there is no accuracy estimate. The protocol in
`docs/methodology/manual_validation.md` fixes the sample, the labelling and
an acceptance criterion before any labelling takes place.

## 8. Limits

- **Selection.** Only organizations with a usable blog domain or a name of
  at least five characters are searched.
- **Coverage of Orbis.** Orbis does not cover all countries and company
  types equally; organizations that are projects, communities or public
  bodies have no company to find.
- **Non-Latin names.** Found only through the domain, and compared through
  the Latin name held in Orbis and a restricted transliteration. Without the
  Latin name, the romanisation often differs from the name a company uses in
  Latin script (`Яндекс` becomes `Iandeks`, similarity 0.62 against `Yandex`).
  On the domain path, name points are not reduced for short or contained
  names (section 4), so only the blacklist of shared platform domains guards
  against matches through a domain used by many companies. The share of Orbis
  names without Latin letters is measured with
  `scripts/diagnostics/count_orbis_non_latin_names.py` (requires access to
  Orbis); the figures are not published until the licence terms for
  aggregate figures are clarified.
- **Entity, not headquarters.** See section 1.
- **Heuristic scores.** See section 4.

## 9. Reproducibility

`--export-csv` writes the outcome for every processed organization (also
those without a result) and, next to it, a metadata file with the run
identifier, the Git commit, the SHA-256 hash of `config/orbis_matching.yaml`,
the Orbis schema, the counts per Match-Status and whether the Latin name
column was read and the comparison switched on (`latin_name_column_used`,
`use_latin_name`). The outcome list carries the
Latin name (`company_latin_name`) and the flag `via_latin_name`; the database
stores only the original name in `company_name`. Outputs that contain Orbis
data are written only to paths that Git ignores. Every run can be rolled back
with `--rollback <run-id>`; rolled-back rows stay in `orbis_enrichments` with
`reverted_at` set.

Tools: `scripts/diagnostics/diagnose_hq_signals.py` (distribution of the
headquarters signals), `scripts/diagnostics/check_orbis_schema.py` (tables
and columns reachable), `scripts/diagnostics/count_orbis_non_latin_names.py`,
`scripts/diagnostics/draw_orbis_validation_sample.py`.

## 10. Data Sensitivity

Orbis is a licensed commercial database. Intermediate files that contain
company names, BvD identifiers or schema details from Orbis (exports, sample
files, review notes, the schema inventory) are excluded from version control
(`workstreams/*/data/`, `results/`, `workstreams/orbis_matching/docs/`); tests
use placeholder identifiers of the form `XX000001`, and a test fails on any
identifier of the BvD form in a tracked file.

The country derived from the web domain (ccTLD), which earlier appeared in
this document, is described in `docs/methodology/country_attribution.md`.
