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
receive no country. The result is stored in `country_code` (ISO 3166-1
alpha-2) and `region`; see `country_attribution.md` for how this value is
used.

## Procedure

A location is resolved in five stages. The first stage that yields a
result ends the procedure.

1. **Cache.** Results are stored in a local file keyed by the normalised
   location string, so that each distinct string is resolved once.
2. **Cleaning.** Additions without geographic meaning (`remote`,
   `home office`, `anywhere`, and similar), URLs, e-mail addresses, and
   brackets are removed. A string that is empty afterwards has no
   country.
3. **Table of special cases.** A maintained table maps about 290
   frequent or problematic strings to a country or marks them as not a
   place, and about 150 country names and aliases to their code. Strings
   that are a URL, a time zone, consist only of emoji or digits, or match
   similar patterns are treated as not a place.
4. **Text rules.** In this order: the name of a US state as the whole
   string, after a comma, or as a separate word; a two-letter US state
   abbreviation at the end; one of 56 large US cities at the beginning; a
   country alias as the whole string; a two-letter country code at the
   end. Sixteen abbreviations that are both a US state and a country code
   (for example `DE`, `IN`, `CO`) count as a US state only if the
   preceding text is a known US city or state; `CA` is not among them and
   always counts as California.
5. **Nominatim.** All remaining strings are sent to the Nominatim
   geocoder of OpenStreetMap, one request per second, with English
   output. The first result is used; its country code and
   state/region are stored.

A location that is not a place, or for which the geocoder returns no
match, is marked with an empty country code, so that it is not requested again in later runs. If
the geocoder does not answer (timeout, rate limit, service error), the
request is repeated once; if it still fails, nothing is stored, the row
stays open, and the run ends with exit code 1.

## Limits

- **The location is voluntary.** Accounts that state no location are
  absent from every analysis by country. Whether they are distributed
  like the accounts that do state one is unknown. The share of accounts
  with a location can be obtained with query 5 of
  `scripts/diagnostics/methodology_checks.sql`.
- **The location is self-reported.** It states where the account holder
  says they are. It is not a nationality, an employer, or a registered
  office, and it is not verified.
- **Several places in one string** (`Berlin / NYC`) are not separated.
  Such strings are resolved as a whole or not at all.
- **Ambiguous names follow fixed rules.** The text rules for US states
  run before the geocoder and take precedence. A string that ends in the
  name or abbreviation of a US state is assigned to the United States,
  also where the same name denotes another place: `Georgia` and
  `Tbilisi, Georgia` are assigned to the United States, as are
  `Perth, WA` (Western Australia) and `London, CA`. In the local cache of
  2026-10-13, 30 of the 104 distinct strings naming one of four Georgian
  cities carried the code `US`. For all other ambiguous names the first
  result of Nominatim decides.
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
every request:

| | Entries | Share |
|---|---:|---:|
| Entries in the list | 1,068 | |
| Result as expected | 1,012 | 94.8% |
| Expected country assigned | 941 of 997 | 94.4% |
| No country assigned although one was expected | 35 of 997 | 3.5% |
| A different country assigned | 21 of 997 | 2.1% |
| Correctly without a country (not a place) | 71 of 71 | 100% |

Of the 962 entries to which the procedure assigned a country, 941 (97.8%)
carried the expected one. By stage: the table of special cases resolved 75
entries, all as expected; the text rules 165, of which 159 as expected;
Nominatim 722, of which 707 as expected.

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

## Cache

The cache stores one entry per normalised location string. Entries
written since 2026-10-12 record how the result was obtained (`source`:
table of special cases, text rule, Nominatim, or no result) and when
(`cached_at`). Entries have no expiry date.
`scripts/diagnostics/inspect_geocoding_cache.py` summarises the cache
without sending requests.

### Caches and Results Written Before 2026-10-12

In earlier versions, a request the geocoder did not answer was stored
like a request without a match: the location entered the cache without a
country, and the row was marked with an empty country code. Every later
account with the same location string then received no country. Entries
that arose in this way cannot be distinguished from genuine misses.
`update_location_geocoding.py --retry-empty` removes all cache entries
without a country and processes the rows marked as not geocodable again.
