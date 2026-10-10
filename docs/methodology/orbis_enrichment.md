# Orbis Enrichment Methodology

Methodology for matching GitHub organizations to companies in the Orbis
(Bureau van Dijk) database and resolving a single headquarters location per
match. See `CONTEXT.md` for the terminology (Match-Status, Match-Type,
Candidate/Contender/Hit, Headquarters Resolution) used throughout this
document, and `docs/usage.md` for the CLI (`scripts/enrichment/orbis_matching_pipeline.py`).

## 1. Matching

1. **Name matching**: GitHub organization name against Orbis `NAME_NATIVE`
   (case-insensitive, indexed).
2. **Blog matching**: GitHub blog-field domain against the Orbis website
   domain.
3. **Plausibility pre-filter**: before scoring, a candidate must reach a
   minimum name-similarity of 0.5 (`difflib.SequenceMatcher`) against the
   GitHub name/login; names shorter than a configured threshold are treated
   as generic/untrustworthy unless corroborated by a blog-domain match.
4. **Scoring** (`config/orbis_matching.yaml`):
   - a name-length-weighted base score — longer, more specific names score
     higher: 40 points at ≥15 characters, 30 at ≥11, 20 at ≥7, 10 below
     that (the rationale, counter-intuitively opposite to "shorter is more
     specific", is that *longer* company names are less likely to collide
     by chance than short/generic ones)
   - +20 points if the blog domain matches the Orbis website domain
   - +10 further points if both name and blog corroborate each other
   - minimum score to count as a match: 30 (reduced to 10 for name-only
     candidates with no blog signal at all — otherwise names under 11
     characters could never reach 30; for that path, the plausibility
     filter and Match-Status, not the score threshold, carry the real
     confidence judgment)
5. Each GitHub organization ends up with a list of scored Orbis candidates.

Scoring and consensus logic is implemented in
`src/github_collector/enrichment/orbis_matching_logic.py`; thresholds and
weights are configured in `config/orbis_matching.yaml`.

## 2. Candidate Filtering and Consensus (Match-Status)

Raw candidates are filtered to contenders (name-plausibility check) and
then to hits (score-threshold check); see `CONTEXT.md` for the exact
definitions. An organization's Match-Status follows from its hits:

- All hits agree on country → `consensus`
- Exactly one hit → `unique`
- Hits disagree, or only an unconfirmed name/login match exists with no
  corroborating signal → `pending` (deliberately not guessed)
- No candidate reaches the minimum score → `no_match`

## 3. Headquarters Resolution

A `pending` organization — multiple disagreeing Orbis entries for what is
otherwise the same matched company — is resolved to a single headquarters
by `src/github_collector/enrichment/hq_detection.py`, via a fixed priority
cascade (`HQ_METHOD_PRIORITY`):

1. **HQ_TABLE** — explicit HQ marking in Orbis (8.3% coverage, high
   reliability)
2. **CONSOLIDATING_ENTITY** — C1/C2 consolidation codes (1.2% coverage)
3. **ROOT_DOMAIN_PRESENCE** — the sole candidate with a genuine root-domain
   website match, among otherwise name-identical candidates (added after a
   finding that Microsoft- and Google-type cases have exactly one entity
   with the real root domain, the rest having none or a subdomain)
4. **OLDEST_FOUNDING** — oldest founding date (71.2% coverage, ~10% error
   rate — see Known Issues)
5. **HIGHEST_REVENUE** — highest revenue (34.5% coverage)
6. **GUO_SELF** — Ultimate Owner self-reference (~30% coverage, 0% accuracy
   for GitHub organizations in manual validation — see Known Issues)
7. **LARGE_CORP_HQ** — company size ≥ 1,000 employees
8. **IS_PARENT** — parent status in `ALL_SUBSIDIARIES_L1` (2.5% coverage)
9. **BVDID_FALLBACK** — alphabetically first BvD ID, preferred within the
   GitHub-derived country if known (100% coverage; a reproducible
   last-resort choice, not a substantive signal)

Before the cascade runs, candidates are (a) filtered by name plausibility
against the GitHub name/login, if available, and (b) deduplicated to one
representative per branch/subsidiary sharing the same base BvD ID. Signals
4 and 5 additionally require a unique winner (no tie) to be used.

### Known Issues

| Issue | Example | Frequency |
|-------|---------|-----------|
| Oldest founding date is not the headquarters | A long-established European legal entity pre-dates a company's actual (later) US headquarters | common |
| Subsidiaries can predate the parent in the record | Local branches sometimes carry an older founding date than the group itself | common |
| Wrong country selected via founding date | ~10% error rate for `OLDEST_FOUNDING` in manual validation | ~10% |
| Ultimate-owner self-reference is unreliable for GitHub orgs | 0% accuracy observed in manual validation | — |

These two findings (`OLDEST_FOUNDING`'s ~10% error rate, `GUO_SELF`'s 0%
accuracy) are why both signals are kept in the cascade but ranked below the
more reliable `HQ_TABLE`, `CONSOLIDATING_ENTITY`, and `ROOT_DOMAIN_PRESENCE`
signals, rather than removed outright — they still contribute coverage
where no stronger signal is available.

## 4. Validation

An early manual-validation round (March 2026, 50 sampled matches) found
54% precision overall, with `OLDEST_FOUNDING` responsible for the majority
of the false positives (51% error rate for country assignment within cases
using that signal). This finding directly motivated adding
`ROOT_DOMAIN_PRESENCE` above it in the cascade and de-prioritizing
`GUO_SELF`.

The scripts used to generate and manage that validation round's
goldstandard dataset have since been superseded and removed; a
goldstandard-based precision
re-measurement against the current cascade has not yet been published.
Batch-level manual review notes from later rounds exist under
`workstreams/orbis_matching/docs/batches/` (not part of the public
repository — see the data-sensitivity note below).

## 5. ccTLD Enrichment

A separate, independent enrichment step derives a country code from the
country-code top-level domain (ccTLD) of an entity's website or email
domain (e.g. `.de`, `.co.uk`). It is applied to organizations by default
and to contributors with `--entity contributors`:

```bash
python scripts/enrichment/enrich_cctld.py
python scripts/enrichment/enrich_cctld.py --entity contributors
```

The procedure is implemented in
`src/github_collector/enrichment/cctld.py` and comprises four rules:

1. The domain is extracted from the `blog` URL and from the `email`
   address; IP addresses and GitHub no-reply addresses are discarded.
2. Compound suffixes (e.g. `.co.uk`, `.com.br`) and single two-letter
   suffixes that are valid ISO 3166-1 alpha-2 codes are mapped to a
   country; `.uk` is mapped to `GB`.
3. Generic TLDs (e.g. `.com`, `.org`, `.net`) and country-code TLDs that
   are predominantly used generically (e.g. `.io`, `.co`, `.ai`, `.me`)
   yield no inference.
4. If the website and the email domain indicate different countries, the
   website takes precedence.

Before any result is written, the extraction is validated against all rows
that already carry a geocoded country code; the run is aborted if the
agreement rate is below 80%. For organizations, rows with an Orbis-derived
country code are excluded from the target population, because the Orbis
value is treated as ground truth. Results are stored in
`country_code_cctld` and do not overwrite the geocoded `country_code`.

## 6. Tools

- `scripts/enrichment/orbis_matching_pipeline.py`: the pipeline entry point (matching
  + headquarters resolution); see `docs/usage.md`.
- `scripts/diagnostics/diagnose_hq_signals.py`: inspect the distribution of
  available HQ signals for multi-hit organizations.
- `scripts/diagnostics/check_orbis_schema.py`: verify that the Orbis tables and columns
  required for headquarters resolution are reachable.

## 7. Data Sensitivity

Orbis/Bureau van Dijk is a licensed commercial dataset. Intermediate
artifacts that contain real company names, BvD IDs, or schema details
derived from Orbis (export/import CSVs, batch review notes, the Orbis
schema inventory) are excluded from version control — see `.gitignore`
entries under `workstreams/*/data/`, `results/`, and the
`workstreams/orbis_matching/docs/` directory.
