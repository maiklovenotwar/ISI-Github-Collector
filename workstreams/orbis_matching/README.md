# GitHub-Orbis Matching

Research and validation workstream behind the Orbis enrichment pipeline
(`scripts/enrichment/orbis_matching_pipeline.py`, `src/github_collector/enrichment/`). See
`docs/methodology/orbis_enrichment.md` and `CONTEXT.md` at the repository root
for the matching/headquarters-resolution methodology and terminology this
workstream developed and validated.

## Note on Scope

This directory's `docs/`, `data/`, and `recovered_docs/` subdirectories
contain real company names, BvD identifiers, and other data derived from the
licensed Orbis (Bureau van Dijk) database — manual batch-review notes,
pilot findings, and intermediate matching results. These are excluded from
version control (see `.gitignore`) because Orbis is a commercially licensed
dataset, not because the underlying work is unpublishable in principle.

## Structure

```
orbis_matching/
├── README.md         — this file
├── docs/             — batch review notes, pilot findings, validation records (excluded, see above)
├── recovered_docs/   — earlier design/strategy documents (excluded, see above)
├── data/              — intermediate candidate/result CSVs (excluded, see above)
└── scripts/
    └── compare_pipeline_with_reviewed_batches.py — one-off verification script that
        compares the output of scripts/enrichment/orbis_matching_pipeline.py with the manually
        reviewed batch results documented in docs/batches/ (not part of the production
        pipeline). The expected assignments are read from data/reviewed_ground_truth.csv
        (columns login, expected, note), which is excluded like the rest of data/.
```
