# Geocoding

Description of how a country is derived from the location an account
states in its GitHub profile, which limits apply, and what is known about
the accuracy. The procedure is implemented in
`src/github_collector/geocoding/`; the entry point is
`scripts/enrichment/update_location_geocoding.py` (see `docs/usage.md`).

## Input

The only input is the profile field `location`. It is optional free text
written by the account holder, for example `Karlsruhe, Germany`,
`Bay Area`, `Earth`, or `Berlin / NYC`. Accounts without a location
receive no country. In the project database on 2026-10-13, 35.7% of the
9,034,973 user accounts and 33.3% of the 735,242 organizations stated a
location; 34.4% and 31.6% respectively had a country. Of the accounts
with a location, 96.2% of the users and 94.8% of the organizations had a
country. The result is stored in `country_code` (ISO 3166-1
alpha-2) and `region`; see `country_attribution.md` for how this value is
used.

### Time of the Location

The location is read at the time a profile is retrieved, and the country
refers to that location. The repositories and their owners were retrieved
between 2025-11-28 and January 2026. The profiles of all user accounts
were retrieved again between 2026-08-28 and 2026-09-06; this overwrote
`location` with the current value but left `country_code` as it was. A
country is determined only for rows without one, so where an account
holder had changed the location in between, the stored country no longer
matched the stored location. A read-only comparison on 2026-10-14 found
this for at most about 1.5% of the accounts with a country and a location
(17,624 with a different country, 30,005 whose current location had never
been geocoded) and 2.5% of the organizations. Since 2026-10-14, a changed
location resets `country_code` and `region` when a profile or an owner is
stored again, so that the geocoding determines them anew. No column
records when a location or a country was determined.

## Procedure

A location is resolved in five stages. The first stage that yields a
result ends the procedure.

1. **Cache.** Results are stored in a local file keyed by the normalised
   location string, so that each distinct string is resolved once. Two
   exceptions: if the table of special cases (stage 3) assigns the string
   to a country, or the table of corrections (stage 3) contains the
   string, that entry takes precedence over the cache and replaces the
   cached result. Both tables are deliberate corrections; before
   2026-10-14 an older geocoder result in the cache could hide them, so
   that `Korea, South` remained assigned to Australia (about 2,800
   accounts and 4,200 organizations). Entries of the table of special
   cases that mark a string as not a place, and the text rules, do not
   override the cache.
2. **Cleaning.** Additions without geographic meaning (`remote`,
   `home office`, `anywhere`, and similar), URLs, e-mail addresses, and
   brackets are removed. A string that is empty afterwards has no
   country.
3. **Table of special cases.** A maintained table maps about 290
   frequent or problematic strings to a country or marks them as not a
   place, and about 150 country names and aliases to their code. Strings
   that are a URL, a time zone, consist only of emoji or digits, or match
   similar patterns are treated as not a place. A second table,
   `src/github_collector/geocoding/data/location_corrections.csv`, lists
   399 strings for which the geocoder had returned a wrong country, each
   checked by hand and stored with the correct country or with none, and
   with the reason (see "Check of Cached Geocoder Results").
4. **Text rules.** In this order: the name of a US state as the whole
   string, after a comma, or as a separate word; a two-letter US state
   abbreviation at the end; one of 56 large US cities at the beginning; a
   country alias as the whole string; a two-letter country code at the
   end. Endings that are ambiguous are treated separately. An ending that
   is both a country code and the abbreviation of a US state, a Brazilian
   or Australian state, or a Canadian province (for example `DE`, `IN`,
   `CO`, `ID`, `PE`, `PR`, `SA`) counts as a US state only if the
   preceding text is a known US city or state; otherwise the string is
   passed to the geocoder (`Recife - PE` is in Brazil, not in Peru).
   `CA` counts as California unless the preceding text names a Canadian
   city or province, in which case it counts as Canada. Strings ending in
   `WA` (Washington or Western Australia) or in the name `Georgia` are
   decided by these rules only if the preceding text is a known US city
   or state, and are otherwise passed to the geocoder. `Georgia` as the
   whole string is assigned to the US state.
5. **Nominatim.** All remaining strings are sent to the Nominatim
   geocoder of OpenStreetMap, one request per second, with English
   output. The first result is used; its country code and
   state/region are stored.

Territories with their own ISO 3166-1 code keep that code, whichever
stage produced the result. Nominatim reports Taiwan, Jersey, or Greenland
under their own code, but Hong Kong and Macau under China, Puerto Rico
and Guam under the United States, and the French overseas regions under
France; the text rules list Puerto Rico and Guam among the US states. A
result whose region or address names such a territory receives the
territory's code (`HK`, `MO`, `PR`, `GU`, `RE`, `GP`, `MQ`, `NC`, `AW`,
`CW`, and others; `src/github_collector/geocoding/territories.py`).

A location that is not a place, or for which the geocoder returns no
match, is marked with an empty country code, so that it is not requested again in later runs. If
the geocoder does not answer (timeout, rate limit, service error), the
request is repeated once; if it still fails, nothing is stored, the row
stays open, and the run ends with exit code 1.

## Limits

- **The location is voluntary.** Accounts that state no location are
  absent from every analysis by country. Whether they are distributed
  like the accounts that do state one is unknown. The share of accounts
  with a location can be obtained with query 3 of
  `scripts/diagnostics/methodology_checks.sql`.
- **The location is self-reported.** It states where the account holder
  says they are. It is not a nationality, an employer, or a registered
  office, and it is not verified.
- **Several places in one string** (`Berlin / NYC`) are not separated.
  Such strings are resolved as a whole or not at all.
- **Ambiguous names follow fixed rules.** The text rules for US states
  run before the geocoder and take precedence. A string that ends in the
  name or abbreviation of a US state is assigned to the United States,
  with the exceptions listed under stage 4. Two conventions remain:
  `Georgia` as the whole string is assigned to the US state, not to the
  country; and a string ending in `CA` is assigned to California unless a
  Canadian city or province from a short list precedes it, so that
  `Ontario, CA` and smaller Canadian places are assigned to the United
  States. `CA` is not passed to the geocoder, because Nominatim reads
  it as the country code of Canada: in a trial it placed Californian
  towns such as `Tracy, CA` in Canada and `Valencia, CA` in Venezuela.
  For all other ambiguous names the first result of Nominatim decides.
- **Geocoder matches are not all checked.** The frequent wrong matches
  found in the check described below are corrected. Rare ones remain:
  strings that name a country but were placed in another and occur only
  once or twice (about 2,300 strings with about 2,600 stored rows), and
  wrong matches for strings without a recognisable country name, which
  cannot be found automatically.
- **Unresolved strings.** In the same cache, 74,032 of 289,920 distinct
  location strings (25.5%) had no country; 5,491 of them are recognised
  as not a place by the current rules. About a quarter of the strings
  without a country (19,276) contain the name of a country as a word and
  were nevertheless not resolved, typically because the string as a whole (a full postal
  address, a sentence, several places) has no match in Nominatim. These
  are shares of distinct strings, not of accounts: a frequent string
  counts once.

## Validation

A list of 1,068 location strings with the expected country, or the
expectation that no country applies, serves as a reference
(`tests/resources/geocoding_gold_standard.json`).
`scripts/maintenance/benchmark_geocoding.py` compares the result of the
procedure with this list.

Result of the run of 2026-10-13, without cache; the geocoder answered
every request. The rule for ambiguous endings, the codes of territories,
and the table of corrections were changed on 2026-10-15; the benchmark
has not yet been repeated with these changes.

| | Entries | Share |
|---|---:|---:|
| Entries in the list | 1,068 | |
| Result as expected | 1,014 | 94.9% |
| Expected country assigned | 943 of 997 | 94.6% |
| No country assigned although one was expected | 35 of 997 | 3.5% |
| A different country assigned | 19 of 997 | 1.9% |
| Correctly without a country (not a place) | 71 of 71 | 100% |

Of the 962 entries to which the procedure assigned a country, 943 (98.0%)
carried the expected one. By stage: the table of special cases resolved 75
entries, all as expected; the text rules 162, of which 158 as expected;
Nominatim 725, of which 710 as expected.

A run on the same day before the rules for ambiguous US state endings were
changed gave 1,012 entries as expected: the change corrected two entries
that had been assigned to the United States (expected: Canada and
Georgia).

Two properties of the list limit what this figure means.

- **The list is not a random sample of accounts.** It was assembled from
  the most frequent strings (30%), strings stratified by country (30%),
  known problematic cases, and a random remainder. It shows how the
  procedure performs on typical and on known difficult input. It does
  not estimate the share of accounts with a correct country.
- **The labels are not independent of the procedure.** During labelling,
  the result of the procedure was displayed as a suggestion and could be
  accepted with a single key press.

An accuracy for the accounts as a whole would require a random sample of
accounts labelled without that suggestion. Such a sample has not been
drawn.

## Check of Cached Geocoder Results

On 2026-10-15 the cache (292,470 strings, 193,576 of them with a
geocoder match) was compared with the stored locations, read-only.
Three kinds of error were found.

- **The string names a country, the geocoder returned another.**
  2,688 strings with 11,027 stored rows, among them `Istanbul/Turkey`
  in Italy (989 rows), `China Beijing` in the United States (677), and
  `Seoul Korea` in Malaysia (247). They were found by searching the
  strings for exactly one country name that differs from the cached
  country. The search also flags strings in which a country name is part
  of a place name (`New Jersey`, `London, Ontario`; 543 strings), which
  were excluded.
- **The string is not a single place, the geocoder returned a country.**
  Continents (`Antarctica`, `Africa`, `North America` in Canada), jokes
  (`/dev/urandom` in Oman), and abbreviations matched to an unrelated
  place (`U.S.A` in the Philippines, `DMV` in India). These cannot be
  found automatically; the most frequent strings among those whose words
  do not occur in the geocoder's address were reviewed by hand.
- **The rule for two-letter endings read state abbreviations as country
  codes.** Compared with geocoder results for the same strings, it was
  wrong more often than right for several endings: `PE` (Pernambuco,
  Brazil) in 1,089 rows against 37, `PR` (Paraná) 1,077 against 58,
  `ID` 699 against 225, `AR` 424 against 55. For the sixteen
  abbreviations that are both a US state and a country code, without a
  known US place before them, the geocoder placed 2,109 strings (22,450
  rows) in the United States and 1,232 (4,230 rows) in the country of the
  code. The rule was changed as described under stage 4.

A general rule ("a separately written country name decides") was
considered and rejected: in a sample it also fired for strings with
several places (`Hamburg / Tokyo`) and for US places such as `Rome, GA`.
Instead, the strings of the first kind with at least three stored rows
(377) and the most frequent strings of the second kind were reviewed by
hand; 399 corrections resulted. Strings that name several countries were
left unchanged, as they are not an error of the geocoder (see "Several
places in one string").

## Cache

The cache stores one entry per normalised location string. Entries
written since 2026-10-12 record how the result was obtained (`source`:
table of special cases, text rule, Nominatim, or no result) and when
(`cached_at`). Entries have no expiry date, so a change of the text rules
does not affect strings that are already cached.
`scripts/diagnostics/inspect_geocoding_cache.py` summarises the cache
without sending requests.

### Caches and Results Written Before 2026-10-13

Until 2026-10-13 the text rules assigned every string ending in
`Georgia`, `WA`, or `CA` to the United States, including
`Tbilisi, Georgia`, `Perth, WA` (Western Australia), and `Toronto, CA`.
In the local cache of that day, 30 of the 104 distinct strings naming one
of four Georgian cities carried the code `US`.
`scripts/maintenance/recheck_ambiguous_locations.py` applies the current
rule to the stored rows whose location has such an ending.

In earlier versions, a request the geocoder did not answer was stored
like a request without a match: the location entered the cache without a
country, and the row was marked with an empty country code. Every later
account with the same location string then received no country. Entries
that arose in this way cannot be distinguished from genuine misses.
`update_location_geocoding.py --retry-empty` removes all cache entries
without a country and processes the rows marked as not geocodable again.

The extent of such entries was estimated on 2026-10-13. Of the 74,032
cache entries without a country, a random sample of 500 was sent to
Nominatim again, and every request was answered. 17 of the 500 (3.4%)
now yielded a country, about five of them evidently wrong (for example
`84 market street` assigned to Australia). The entries without a country
are therefore almost entirely strings that cannot be resolved, not
failures of the geocoder. `--retry-empty` was not run on the project
database.
