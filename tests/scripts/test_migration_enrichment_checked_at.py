"""Steps 2 and 3 of add_organizations_enrichment_checked_at.sql on SQLite (task 9, E22).

The steps move the timestamps of the GitHub profile fetch from orbis_enriched_at to
enrichment_checked_at. They must not overwrite a newer enrichment_checked_at, must
change nothing when run a second time, and must be reversible from the CSV backup.
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

MIGRATION = (
    Path(__file__).parent.parent.parent
    / "scripts"
    / "migrations"
    / "add_organizations_enrichment_checked_at.sql"
)


def statements():
    lines = MIGRATION.read_text(encoding="utf-8").splitlines()
    body = "\n".join(line for line in lines if not line.strip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def restore_statement():
    for line in MIGRATION.read_text(encoding="utf-8").splitlines():
        stripped = line.strip().lstrip("-").strip()
        if stripped.startswith("UPDATE organizations SET orbis_enriched_at = :orbis_enriched_at"):
            return stripped.rstrip(";")
    raise AssertionError("restore statement missing in the migration file")


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE organizations (id INTEGER PRIMARY KEY, orbis_enriched_at TEXT, "
                "orbis_run_id TEXT, bvd_id TEXT, enrichment_checked_at TEXT)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO organizations VALUES "
                "(1, '2026-08-01', 'gh-run', NULL, NULL), "  # fetch timestamp to move
                "(2, '2026-08-01', 'gh-run', NULL, '2026-10-16'), "  # newer value already set
                "(3, '2026-08-02', 'orbis', 'XX000001', NULL), "  # an Orbis match, untouched
                "(4, NULL, NULL, NULL, NULL)"
            )
        )
    return engine


def rows(engine):
    with engine.connect() as conn:
        return {
            r[0]: tuple(r[1:])
            for r in conn.execute(
                text("SELECT id, orbis_enriched_at, enrichment_checked_at FROM organizations")
            )
        }


def run_steps_2_and_3(engine):
    step_2, step_3 = statements()[1:3]
    with engine.begin() as conn:
        changed = conn.execute(text(step_2)).rowcount + conn.execute(text(step_3)).rowcount
    return changed


def test_steps_move_the_fetch_timestamp_but_keep_a_newer_value(engine):
    run_steps_2_and_3(engine)

    assert rows(engine) == {
        1: (None, "2026-08-01"),
        2: (None, "2026-10-16"),  # the newer value stays
        3: ("2026-08-02", None),
        4: (None, None),
    }


def test_second_run_changes_nothing(engine):
    run_steps_2_and_3(engine)
    assert run_steps_2_and_3(engine) == 0


def test_restore_from_backup_gives_the_original_state(engine):
    before = rows(engine)
    with engine.connect() as conn:
        backup = [
            dict(r._mapping)
            for r in conn.execute(
                text(
                    "SELECT id, orbis_enriched_at, enrichment_checked_at FROM organizations "
                    "WHERE orbis_enriched_at IS NOT NULL"
                )
            )
        ]
    run_steps_2_and_3(engine)

    with engine.begin() as conn:
        for record in backup:
            conn.execute(text(restore_statement()), record)

    assert rows(engine) == before
