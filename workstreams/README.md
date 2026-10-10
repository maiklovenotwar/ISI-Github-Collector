# Workstreams

Ongoing analyses that build on the collector database but are not part of
the collection and enrichment pipeline. The pipeline itself lies in
[`scripts/`](../scripts/README.md) and `src/github_collector/`.

Each workstream has its own `README.md` with its question, status, and
findings. The scripts read from the database and do not write to it. Input
and intermediate data lie in a `data/` folder that is excluded from version
control, because they contain data derived from the licensed Orbis database
or samples that are not published; the scripts can therefore be read, but
not all of them can be run from a fresh checkout. The column *Needs* names
what a script requires besides the repository.

## `entity_classification/`

**Question:** can GitHub organizations be classified as company, research
institution, government agency, NGO, community project, or individual?

**Status (2026-10-07):** exploratory analysis complete: a population
estimate from 1,000 individually classified organizations, a rule-based
prototype, and a comparison of language models against the classified
sample. No production code. See
[`entity_classification/README.md`](entity_classification/README.md).

| Script | Purpose | Needs |
|--------|---------|-------|
| `entity_classification/scripts/build_goldstandard_samples.py` | Merges the four raw classification batches into the population sample (n=1,000) and the rule-validation sample (n=100). | raw batches in `data/goldstandard/` |
| `entity_classification/scripts/classify_organizations_by_rules.py` | Applies the rule-based classifier (keyword lists and a lookup in the ROR registry) to all organizations and writes the summary and examples under `results/`. | database (read), ROR data dump in `ror/` |
| `entity_classification/scripts/evaluate_rules_on_goldstandard.py` | Measures precision and recall of the rule-based classifier on the rule-validation sample. | database (read), ROR data dump, `data/goldstandard/` |
| `entity_classification/scripts/evaluate_llms_on_goldstandard.py` | Has language models classify a sample of the goldstandard and compares the answers with its labels; one CSV per model run in `data/llm_evaluation/`. | database (read), `data/goldstandard/`, access to an OpenAI-compatible model server (see `.env.template`) |
| `entity_classification/scripts/plot_goldstandard_distribution.py` | Builds the Excel workbook with the category distribution of the goldstandard under `results/`. | `data/goldstandard/`, the package `openpyxl` |

## `orbis_matching/`

**Question:** how reliably can GitHub organizations be matched to companies
in the Orbis database, and how is the headquarters country determined when
several Orbis entries qualify?

**Status:** the method developed here is implemented in
`scripts/enrichment/orbis_matching_pipeline.py` and described in
[`docs/methodology/orbis_enrichment.md`](../docs/methodology/orbis_enrichment.md).
Validation against manually reviewed batches is ongoing; the review notes
are not published. See [`orbis_matching/README.md`](orbis_matching/README.md).

| Script | Purpose | Needs |
|--------|---------|-------|
| `orbis_matching/scripts/compare_pipeline_with_reviewed_batches.py` | Runs the matching for the organizations of the manually reviewed batches and compares the result with the reviewed assignment. | database (read), Orbis database (VPN), `data/reviewed_ground_truth.csv` |
