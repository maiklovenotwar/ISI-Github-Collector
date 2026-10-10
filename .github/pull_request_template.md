## What and why

<!-- What does this change do, and what problem does it solve? Link the issue if there is one. -->

## How it was tested

<!-- Commands you ran and what you observed, e.g. `pytest`, a dry run of a script. -->

## Checklist

- [ ] `pytest` passes locally
- [ ] `pre-commit run --all-files` passes locally
- [ ] Documentation updated where behavior, CLI flags, or the database schema changed
      (`README.md`, `docs/`, `CONTEXT.md` for Orbis-matching terminology)
- [ ] Schema changes come with a migration under `scripts/migrations/` and a matching
      update to `src/github_collector/database/models.py`
- [ ] `CHANGELOG.md` has an entry under "Unreleased"
- [ ] No licensed or personal data added (Orbis/BvD exports, contributor dumps, tokens)
