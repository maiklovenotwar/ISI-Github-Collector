# Manual Validation Protocol

Protocol used to manually assess the accuracy of GitHub-to-Orbis matching
and headquarters (HQ) detection against a hand-reviewed sample
(a "goldstandard"). See `docs/methodology/orbis_enrichment.md` for the
matching/HQ-resolution methodology being assessed, and `CONTEXT.md` for
terminology.

The protocol assesses two independent questions per sampled organization:

1. **Match correctness**: do the GitHub organization and the selected
   Orbis company refer to the same real-world entity?
2. **HQ correctness** (only meaningful if the match is correct): does the
   algorithm's selected headquarters country match the company's actual
   headquarters?

## Validation Columns

### Match Assessment

| Column | Values | Description |
|--------|--------|--------------|
| `manual_match_correct` | TRUE / FALSE | Do the GitHub org and Orbis company refer to the same entity? |
| `manual_match_reasoning` | text | Free-text justification (e.g. matching name and website; different industries) |
| `manual_match_confidence` | HIGH / MEDIUM / LOW | Confidence in the match judgment |
| `algorithm_match_assessment` | TP / FP / TN / FN | Confusion-matrix classification of the algorithm's match decision against the manual one |

### HQ Assessment

| Column | Values | Description |
|--------|--------|--------------|
| `manual_hq_country` | ISO-2 country code | Researched actual headquarters country |
| `manual_hq_reasoning` | text | Source and justification |
| `manual_hq_confidence` | HIGH / MEDIUM / LOW | HIGH: official source (company site, regulatory filing); MEDIUM: indirect evidence (founding location, CEO location); LOW: no clear information found |
| `manual_hq_data_source` | text | Specific source used |
| `algorithm_hq_assessment` | TP / FP / TN / FN | Algorithm country vs. manual country |

### Overall

| Column | Values | Description |
|--------|--------|--------------|
| `algorithm_overall_correct` | TRUE / FALSE | True only if both the match and the HQ country are correct |
| `error_category` | MATCH_ERROR / HQ_ERROR / BOTH / NONE | Where the error, if any, is located |
| `difficulty_level` | EASY / MEDIUM / HARD | Subjective difficulty of the validation case |
| `validation_notes`, `validator_name`, `validation_date`, `validation_time_minutes` | — | Audit trail and effort tracking |

## Data Sources, in Order of Preference

1. Official company website ("About", "Contact", "Locations")
2. Regulatory filings (e.g. SEC 10-K/10-Q for US companies)
3. Company registers (e.g. Companies House in the UK, Handelsregister in
   Germany)
4. Annual reports / investor relations materials
5. Wikipedia and LinkedIn — treated as backup sources only, and always
   cross-checked against one of the above

## Workflow

1. Sample organizations with multiple Orbis candidates (stratified by
   company size/type where possible, e.g. well-known "tech giant" cases
   first as an easy baseline, then progressively less well-documented
   companies).
2. For each case, judge match correctness first; only proceed to HQ
   validation if the match is judged correct.
3. Classify the algorithm's decisions against the manual judgment using the
   confusion-matrix columns above.
4. Categorize any error and estimate difficulty.
5. Record validator, date, and time spent.

## Quality Control

- **Double validation**: a subset of cases (e.g. 10%) is independently
  re-validated by a second reviewer.
- **Inter-rater reliability**: agreement between validators is measured on
  the double-validated subset.
- **Consistency checks**: the same company should receive the same
  judgment wherever it recurs in the sample.
- Cases marked `LOW` confidence are reviewed again before being treated as
  final.

## Common Pitfalls

- **Name collisions**: a matching name does not guarantee the same company
  (e.g. an unrelated company that happens to share a word with a
  well-known one).
- **Subsidiary vs. parent**: Orbis frequently surfaces a subsidiary entity
  rather than the ultimate parent.
- **Headquarters relocation**: use the most current information; a
  company's HQ can have moved since an older source was published.
- **Multiple offices**: the official, legally registered headquarters is
  the target — not simply the largest office.

## Resulting Metrics

From the validated sample:

- **Match accuracy**: share of cases with `manual_match_correct = TRUE`
- **HQ accuracy**: share of correctly-matched cases with
  `algorithm_hq_assessment = TP`
- **Overall accuracy**: share with `algorithm_overall_correct = TRUE`
- Accuracy can be further broken down by the HQ-resolution signal used
  (`hq_method_used`) and by validator confidence, to identify which
  signals in the priority cascade (see `docs/methodology/orbis_enrichment.md`)
  are driving errors

## Status

This protocol was used for the goldstandard validation rounds described in
`docs/methodology/orbis_enrichment.md` (Section 4). The scripts that
generated and managed those goldstandard datasets have since been
superseded and removed; the protocol itself remains applicable to any
future validation round.
