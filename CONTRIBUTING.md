# Contributing

## Setup

Installation (`pip install -e ".[dev]"`) and database configuration are described in
`README.md`. The git hooks are installed once per clone:

```bash
pre-commit install
```

## Workflow

1. Each defect or change is described in an issue before work begins, so that planned work is
   visible to all contributors.
2. Work takes place on a branch created from `main` and named after the kind of change:
   `feature/<short-topic>`, `fix/<short-topic>`, `docs/<short-topic>`, or
   `refactor/<short-topic>`.
3. Commits are small and self-contained. Commit messages use the
   [Conventional Commits](https://www.conventionalcommits.org/) prefixes `feat:`, `fix:`,
   `docs:`, `refactor:`, `test:`, `chore:`, and `data:`.
4. Changes are merged through a pull request against `main`, using the pull request template.
   Merging requires a passing CI run and one approving review; the code owners listed in
   `.github/CODEOWNERS` are requested as reviewers automatically. Authors do not approve their
   own pull requests.
5. Every change that is observable to users of the collector is recorded in the "Unreleased"
   section of `CHANGELOG.md`.

## Running Tests

```bash
pytest
```

Tests that need live GitHub tokens (`external`) or VPN access to the Orbis database
(`orbis_integration`) are deselected by default. See `docs/testing.md` for the full test suite
layout, markers, and coverage commands.

## Code Style

`black` and `ruff` are configured in `pyproject.toml` and run through
`.pre-commit-config.yaml`, together with `detect-secrets`. Run all hooks manually with:

```bash
pre-commit run --all-files
```

Functions that predate the configured complexity limit are marked `# noqa: C901`. New
exemptions are not added; functions exceeding the limit are split instead.

`mypy` is configured in `pyproject.toml` but is not part of the hooks or CI yet -- running it is
optional for now.

## Continuous Integration

`.github/workflows/ci.yml` runs on every pull request and on pushes to `main` and `develop`:

- **Lint**: the pre-commit hooks (ruff, black, detect-secrets)
- **Tests**: `pytest` with coverage on Python 3.9

## Where Things Go

| Kind of change | Location |
|---|---|
| Library code | `src/github_collector/<module>/` |
| Command-line entry points | `scripts/<stage>/` (`collection`, `enrichment`, `export`, `maintenance`), listed in `scripts/README.md` |
| Database schema changes | `scripts/migrations/` plus `src/github_collector/database/models.py` |
| Manual diagnostics against live systems | `scripts/diagnostics/` (never under `tests/`) |
| Automated tests | `tests/`, mirroring the module under test |
| Research analyses built on the collected data | `workstreams/<name>/` with its own `README.md`, listed in `workstreams/README.md` |

Scripts written for a single use are removed after that use; they remain available in the
git history.

## Data Policy

Licensed and personal data are not committed. This covers Orbis/Bureau van Dijk exports and
match results, exports of contributor or organization records, API tokens, and `.env` files.
The affected paths are listed in `.gitignore`, and the `detect-secrets` hook rejects commits
that contain tokens. The rationale is given under "Data Sensitivity" in
`docs/methodology/orbis_enrichment.md`.

Some code comments cite `Orbis-Notizen (intern)` followed by a file name. These are review
notes of the Orbis matching that contain licensed data and are therefore not part of the
repository. The comment states the finding itself; the note is only its internal source.

## Documentation

`docs/` is organized into `docs/architecture/` (system design, database schema, API client,
Pydantic schemas) and `docs/methodology/` (the Orbis matching/headquarters-resolution approach
and its validation). See `CONTEXT.md` for the domain terminology used throughout. Decisions with
lasting consequences are recorded under `docs/decisions/`.

## Releases

The version lives in `pyproject.toml` and follows [Semantic Versioning](https://semver.org/).
A release consists of three steps: the "Unreleased" entries in `CHANGELOG.md` are moved under
a new version heading, the version in `pyproject.toml` is incremented, and the merge commit is
tagged `v<version>`.
