# Validation Protocol for the Orbis Matching

Protocol for estimating how often the matching of GitHub organizations to
companies in the Orbis database (Bureau van Dijk) assigns the right company
and the right country. It is fixed before any labelling, so that the
procedure cannot be adjusted to the result. See
`docs/methodology/orbis_enrichment.md` for the matching itself and
`CONTEXT.md` for the terminology (Match-Status, Match-Type).

**Status:** no validation round has been carried out under this protocol.
Until one has passed the acceptance criterion below, `country_code_orbis`
is not used in analyses.

## 1. Reference Run

A validation round refers to exactly one run of
`scripts/enrichment/orbis_matching_pipeline.py`, identified by the metadata
file that `--export-csv` writes next to its outcome list (`run_id`, Git
commit, SHA-256 of `config/orbis_matching.yaml`, Orbis schema, start time,
counts per Match-Status). The results that are written to the database are
validated by drawing the sample from the outcome list of the same run, or
of a dry run with the same commit and configuration hash. A later change to
the code or the configuration requires a new round.

## 2. Population and Strata

The population is every organization the run processed, that is, every
organization in `organizations` without an Orbis result. Each organization
falls into one stratum, taken from the outcome list:

| Stratum | Definition | Drawn |
|---|---|---:|
| `unique` | Match-Status `unique` | 50 |
| `consensus` | Match-Status `consensus` | 50 |
| `pending_resolved` | Match-Status `pending`, headquarters resolution chose a candidate | 50 |
| `pending_unresolved` | Match-Status `pending`, no corroborated candidate | 100 |
| `searched_no_candidate` | searched by domain or name, Match-Status `no_match` | 100 |
| `not_searchable` | neither a usable blog domain nor a name or login of at least five characters | counted only |

In addition, the cases matched through the transliteration of a non-Latin
company name, and those found by the name or login fallback, are
oversampled from the remaining cases (up to 50 each) and evaluated on their
own. Organizations whose cases were used to develop the rules (the review
batches of 2026) are excluded and listed in an ignored file passed with
`--exclude-logins`.

`scripts/diagnostics/draw_orbis_validation_sample.py` draws the sample with
a fixed seed, without replacement within each stratum, and numbers the cases
in random order.

## 3. Questions per Case

The labeller answers three questions in this order.

1. **Is the organization a company that could be listed in Orbis?** One of
   `company`, `project`, `community`, `university or research institute`,
   `public body`, `unclear`. An organization that is not a company but was
   assigned an Orbis entity counts as a wrong assignment; the shares of the
   categories are reported per stratum.
2. **Is the assigned Orbis entity the same company?** `yes`, `no`, or `no
   entity assigned`. For a case without an assignment: does the company
   exist in Orbis (`missed`) or not (`absent`)?
3. **Is `country_code_orbis` the country of the company's actual
   headquarters?** Answered for every case with an assignment, also when
   the entity is a subsidiary of the right company.

Sources, in order of preference: the company's own website (imprint,
"About", "Contact"); a company register (Companies House, Handelsregister,
SEC filings); annual reports. Wikipedia and LinkedIn only as a supplement.
Every answer names at least one source outside Orbis.

## 4. Procedure

1. **Blind stage.** The labeller receives only the blind file (`case_id`,
   login, name, blog, GitHub country) and answers question 1, identifies the
   company from the sources above and searches Orbis for it independently.
2. **Comparison stage.** Only after the blind stage is complete is the key
   file opened. The labeller answers questions 2 and 3 against the assigned
   entity and records both the blind identification and the final judgment.
   Disagreements between them are resolved by a second person.

The independent Orbis search shares some blind spots with the procedure (the
same website field, names in non-Latin script). Companies that both miss are
not counted as missed, so the share of missed companies is underestimated.

## 5. Double Labelling

At least 40 cases, spread across all strata, are labelled independently by
two people. Reported are the raw agreement and Cohen's kappa with a 95%
interval for each of the three questions. Disagreements are adjudicated and
the adjudicated value is used.

## 6. Estimates

- **Precision per stratum:** among the cases with an assigned entity, the
  share with question 2 = `yes` (an organization that is not a company counts
  as `no`); likewise the share with question 3 = `yes`. Cases answered
  `unclear` in question 1 are reported and excluded from the denominator.
  Each estimate with a 95% Wilson interval; at 50 cases the interval is at
  most ±14 percentage points wide.
- **Weighted precision of the written results:** over the strata with an
  assignment (`unique`, `consensus`, `pending_resolved`),
  p = Σ_h W_h · p_h with W_h = N_h / N, variance
  Σ_h W_h² · p_h (1 − p_h) / (n_h − 1) · (1 − n_h / N_h).
  Reported next to the per-stratum values.
- **Missed companies:** only as a bound, from `pending_unresolved` and
  `searched_no_candidate` (share `missed`, with Wilson interval), and with
  the caveat of section 4. `not_searchable` is reported as a count; for these
  organizations the procedure cannot find a company.
- The oversampled groups are evaluated separately and are not mixed into the
  weighted estimate.

## 7. Acceptance Criterion

Fixed before labelling: a stratum is used in analyses only if the lower bound
of the 95% Wilson interval of its precision is at least 0.80, both for the
company (question 2) and the country (question 3). A stratum below the
criterion is not used, also if the overall estimate passes.

## 8. Recorded Columns

| Column | Values |
|---|---|
| `case_id` | from the sample files |
| `entity_kind` | question 1 |
| `blind_identification` | company and source found in the blind stage |
| `orbis_entity_correct` | question 2 |
| `hq_country_actual` | ISO 3166-1 alpha-2 code from the sources |
| `hq_country_correct` | question 3 |
| `hq_method_used` | from the key file (`hq_method`) |
| `sources` | at least one source outside Orbis |
| `labeller`, `date`, `minutes` | audit trail |
| `second_labeller`, `adjudicated_value` | for double-labelled cases |

The labelled files contain Orbis data and stay in ignored paths
(`workstreams/orbis_matching/data/`). Only the aggregated results are
published.
