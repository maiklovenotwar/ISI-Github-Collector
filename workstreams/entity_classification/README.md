# GitHub Entity Classification

**Objective:** analyze how GitHub organizations can be classified as a **research
institution**, a **company**, or an **individual contributor** (requested by the project lead,
2026-07-24). During the investigation, three further categories emerged as necessary and
distinct: **government agency**, **NGO/nonprofit**, and **community/informal project** (see
[docs/taxonomy.md](docs/taxonomy.md)).

**Status (2026-10-07):** exploratory analysis complete; no production code, no writes to the
production database. Three results exist:

1. a population estimate from n=1,000 individually classified organizations,
2. a rule-based prototype (ROR + keywords) that is precise but recall-limited,
3. a comparison of local and Fraunhofer-hosted LLMs against the goldstandard (2026-08-19/27),
   best models at ~69% agreement.

Open: the decision on a paid LLM batch run ([docs/next_steps.md](docs/next_steps.md)), and
whether repository content should be added as a classification signal (see
[Repository Content as a Fallback Signal](#repository-content-as-a-fallback-signal)).

## Headline Finding

Of the 735,242 GitHub organizations in the collector database, the estimated breakdown (from an
n=1,000 random sample, no formal confidence interval — see
[docs/population_estimate.md](docs/population_estimate.md)) is:

| Category | Share | Extrapolated |
|---|---:|---:|
| Company | 22.3% | ~164,000 |
| Community/informal project | 20.3% | ~149,000 |
| Research | 16.9% | ~124,000 |
| Individual contributor (despite the "Organization" account type) | 3.7% | ~27,000 |
| NGO/nonprofit | 1.7% | ~13,000 |
| Government agency | 1.0% | ~7,000 |
| **Not classifiable** (insufficient signal) | **34.1%** | **~251,000** |

The three originally requested categories cover only 43% of the population.

**Caveat on the labels:** the 1,000 cases were classified case by case in a Claude Code session
(see [docs/next_steps.md](docs/next_steps.md)), drawing on world knowledge beyond the profile
fields — not by an independent human rater. A blind re-check of 10 cases gave 80%
self-consistency ([docs/goldstandard_methodology.md](docs/goldstandard_methodology.md)). Every
agreement figure below is measured against these labels and inherits that uncertainty.

## Rule-Based Classifier

A rule-based classifier (keyword lists + lookup against the [ROR database](https://ror.org) for
research institutions) was built as a prototype and applied to the full database. It assigns a
category to 5.5% of all organizations, with no false positives observed in spot checks, but
reaches only **~26-53% recall** for research/government/NGO (depending on category and sample)
and does not cover company/community/individual **at all** — see
[docs/rule_based_classifier.md](docs/rule_based_classifier.md). It is usable as a conservative
pre-filter, not as a full classification.

## LLM Comparison

`scripts/evaluate_llms_on_goldstandard.py` sends the profile fields (login, name, company, bio, blog,
location, email) of goldstandard organizations to OpenAI-compatible chat models and compares the
answer with the goldstandard label. Two providers: a local LM Studio server and FhGenie
(Fraunhofer's internal LLM gateway). Sample: stratified, 10 organizations per category (n=70,
seed 42) — so the figures are **not** population-weighted accuracy.

Runs of 2026-08-27 (`data/llm_evaluation/<provider>_<model>.csv`):

| Model | Provider | Agreement | Unparseable answers |
|---|---|---:|---:|
| qwen3.6-27b | local | 68.6% | 7 |
| gemma-4-31b | local | 68.6% | 0 |
| GLM-5.2-NVFP4 | FhGenie | 64.3% | 7 |
| DeepSeek-V4-Flash | FhGenie | 64.3% | 0 |
| qwen3.6-35b-a3b | local | 60.0% | 15 |
| MiniMax-M2.5 | FhGenie | 54.3% | 5 |
| Teuken-7B-instruct | FhGenie | 15.7% | 0 |

Observations:

- **Community is the weak category for every model** (1-4 of 10 correct); these organizations
  are mostly answered with "Unklar". Individual varies widely (2-9 of 10).
- Company and "not classifiable" are recognized well by most models (8-10 of 10).
- Teuken-7B answers "Unklar" for 69 of 70 organizations.
- Unparseable answers (empty response or API error) count as disagreement, which understates
  qwen3.6-35b-a3b in particular.
- The three local models were also run on 2026-08-19 (files without provider prefix); results
  differ by 1.5-3 percentage points from the 08-27 runs, which indicates the run-to-run noise.
- Two small models were tried earlier on a different, proportional sample (n=150):
  llama-3.2-3b 54.7%, qwen3-4b 44.7%. Not comparable with the table above.

## Repository Content as a Fallback Signal

Checked on 2026-10-07: can the content of an organization's repositories (READMEs, descriptions)
reduce the "not classifiable" share? The collector database stores repository descriptions but
no READMEs; READMEs were fetched read-only from the GitHub API for the check.

**Reach.** Of the 341 goldstandard organizations labeled "not classifiable", 217 (64%) have
`public_repos = 0` — nothing to read. Across the whole goldstandard, 524 of 1,000 organizations
have no public repository. Repository content can therefore help at most ~36% of the
unclassifiable cases (~12% of the population).

**Yield.** For a random 45 of the remaining 124 "not classifiable" organizations, the two
most-starred non-fork repositories were read (README + description):

| Outcome after reading repository content | Count |
|---|---:|
| Clear category | 28 |
| Weak tendency only | 10 |
| Still not classifiable | 7 |

The 28 clear cases split into Community (18), Research — mostly papers and university course
projects (6) — and Individual (4). **None** became Company, Government agency, or NGO. Roughly
two thirds of the clear cases were already decidable from repository names and descriptions,
which are in the database; the README itself was needed for about one third. Organization
profile READMEs (`.github/profile/README.md`) exist for only 8 of 68 organizations checked.

**Assessment.** Worthwhile as a second stage for organizations that remain unclassifiable, not
as a general input: extrapolated, the unclassifiable share would drop from ~34% to roughly
26-28% (n=45, so ±14 percentage points on the yield). Limits:

- It adds nothing for the Company category and risks a bias towards Community: repositories
  show code, not legal form, so a small company with an empty profile looks like an open-source
  project.
- The goldstandard was labeled without repository content. A classifier that uses it will
  "disagree" with correct-but-outdated "Unklar" labels, so those ~124 organizations need
  relabeling with the same information before any agreement figure is meaningful.
- The check above was a manual reading, not an LLM run.

Suggested order: first add repository names/descriptions/languages from the database (no API
calls), measure, then fetch READMEs only for what is still unclassifiable (~90,000
organizations, 2-3 API requests each).

### Measured: repository descriptions from the database (2026-10-07)

First step of that order, run with `evaluate_llms_on_goldstandard.py --sample with-repos --variant both`:
all 438 goldstandard organizations that have non-fork repositories in the collector database,
each classified twice by gemma-4-31b (local) — from the profile fields only, and with the five
most-starred repositories (name, language, stars, description) appended. No READMEs yet.

| Goldstandard category | n | Profile only | With repositories |
|---|---:|---:|---:|
| Research | 76 | 68.4% | 88.2% |
| Company | 119 | 84.9% | 81.5% |
| Government agency | 6 | 83.3% | 83.3% |
| NGO/nonprofit | 9 | 33.3% | 33.3% |
| Community | 107 | 35.5% | 61.7% |
| Individual | 12 | 33.3% | 41.7% |
| Not classifiable | 109 | 89.9% | 56.0% |
| **All** | **438** | **68.7%** | **69.4%** |

- Research and Community gain 20 and 26 percentage points, almost entirely from cases that
  were answered "Unklar" without repositories. Excluding the "not classifiable" rows, agreement
  rises from 61.7% to 73.9% (n=329).
- The drop for "not classifiable" is the relabeling problem described above, not a measured
  model error: with repositories, 48 of 109 receive a category (32 Community, 8 Individual,
  5 Research, 3 Company), which the goldstandard counts as wrong. These 48 have not been checked
  individually yet, so the overall figure (69.4%) understates the variant by an unknown amount.
- The Community bias is visible but small: 14 companies are classified as Community with
  repositories, against 3 without.
- NGO, Government agency and Individual are too small (6-12 cases) to read anything from.
- One model only; qwen3.6-27b was not loaded on the local server and FhGenie rejected the token
  (HTTP 401) at the time of the run.

Results: `data/llm_evaluation/local_google_gemma-4-31b__withrepos-sample.csv` (profile
only) and `...__withrepos-sample__repos.csv`. In the first pass 18 and 35 answers came back
empty (token budget exhausted before the answer); they were re-asked with a larger budget, which
the script now does automatically.

## Structure

```
entity_classification/
├── README.md                          — this file
├── docs/
│   ├── taxonomy.md                    — categories, definitions, edge cases
│   ├── category_definitions_examples.md — management version of the taxonomy, with examples
│   ├── goldstandard_methodology.md    — how the 1,000-case sample was constructed, quality controls
│   ├── population_estimate.md         — final figures + convergence across sample sizes
│   ├── rule_based_classifier.md       — the ROR/keyword rule: design, bugs found, precision/recall
│   └── next_steps.md                  — the LLM-batch option, cost estimate, recommendation
├── data/                              — git-ignored
│   ├── goldstandard/
│   │   ├── population_sample_1000.csv     — n=1,000, purely random, basis for the population estimate
│   │   ├── rule_validation_sample_100.csv — n=100 (segments A/B/C), basis for the rule's precision/recall
│   │   └── raw_batch*.csv                 — unmodified raw data from the four classification batches
│   └── llm_evaluation/                — one CSV per model run (login, label, raw answer, parsed, match)
├── ror/                               — git-ignored; ROR data dump, see Data Provenance
├── scripts/
│   ├── build_goldstandard_samples.py      — builds the two goldstandard CSVs from the raw_batch* files
│   ├── classify_organizations_by_rules.py — applies the rule to the full organizations table (read-only)
│   ├── evaluate_rules_on_goldstandard.py  — tests the rule against rule_validation_sample_100.csv
│   ├── evaluate_llms_on_goldstandard.py   — runs the LLM comparison against the goldstandard
│   └── plot_goldstandard_distribution.py  — builds the Excel chart of the category distribution
└── results/
    ├── rule_classification_summary.txt    — bucket distribution of the rule-based run over all 735K orgs
    ├── rule_classification_examples.csv   — sample hits per category, for spot-checking
    └── goldstandard_kategorienverteilung.xlsx — category distribution of the goldstandard (data + chart)
```

The docs under `docs/` are written in German.

## Running the Scripts

All scripts except `build_goldstandard_samples.py` and `plot_goldstandard_distribution.py` read
`DATABASE_URL` from the environment (load the repository's `.env` first) and only read from the
database.

- `classify_organizations_by_rules.py`, `evaluate_rules_on_goldstandard.py`: need the ROR dump under `ror/`.
- `evaluate_llms_on_goldstandard.py`: additionally needs `LOCAL_LLM_URL`, `FREE_LLMS_URL` and `FREE_LLMS`
  (FhGenie token); see `.env.template`. Models are configured in `MODEL_RUNS`.
- `build_goldstandard_samples.py`: reads and writes `data/goldstandard/` relative to its own location.
- `plot_goldstandard_distribution.py`: needs `openpyxl`, which is not among the project dependencies.

## Data Provenance

- **GitHub collector database**: `organizations` table, 735,242 rows, as of 2026-07-24 (MySQL,
  see `.env` in the project root for connection details).
- **ROR (Research Organization Registry)**: `v2.10-2026-07-20-ror-data.csv`, 132,537 entries,
  CC0-licensed, downloaded from [Zenodo](https://doi.org/10.5281/zenodo.6347574). The file
  (~52 MB) is expected at `ror/` and is excluded from version control — re-download from Zenodo
  if needed.
- **Version control**: `data/` and `ror/` are git-ignored, so the goldstandard and the LLM
  comparison results exist only locally.

## Relationship to `orbis_matching`

This classification is complementary to the Orbis matching workstream: once that company
matching pipeline runs reliably, a *confirmed match* there is, in practice, the definition of
"company" here — the two workstreams should be merged in the medium term (see
[docs/next_steps.md](docs/next_steps.md)). This matters all the more because repository content
does not help identify companies (see above).
