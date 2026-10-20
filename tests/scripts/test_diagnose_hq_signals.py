"""Query 4 of diagnose_hq_signals.py reads the matches from the collector database.

Functional test 2026-10-20: the query read a staging table in Orbis from an earlier workflow
that no longer exists, so the script ended with ORA-00942.
"""

import importlib.util
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, insert, update

from github_collector.database.models import Base, OrbisEnrichment, Organization

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "diagnostics" / "diagnose_hq_signals.py"


def load_script():
    spec = importlib.util.spec_from_file_location("diagnose_hq_signals", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_matched_ids_are_the_current_matches_of_the_organizations(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'collector.db'}")
    Base.metadata.create_all(engine)
    match = {
        "match_type": "name_only",
        "score": 90,
        "run_id": "r",
        "enriched_at": datetime(2026, 1, 1),
    }
    with engine.begin() as conn:
        conn.execute(
            insert(Organization.__table__), [{"id": 1, "login": "a"}, {"id": 2, "login": "b"}]
        )
        conn.execute(
            insert(OrbisEnrichment.__table__),
            [
                {"id": 10, "organization_id": 1, "bvd_id": "XX1", **match},
                {"id": 11, "organization_id": 2, "bvd_id": "XX2", **match},  # rolled back
            ],
        )
        conn.execute(
            update(Organization.__table__)
            .where(Organization.id == 1)
            .values(current_orbis_enrichment_id=10)
        )

    assert load_script().matched_bvd_ids(engine) == ["XX1"]


class FakeCursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, binds):
        self.calls.append((sql, dict(binds)))
        self.last = (sql, binds)

    def fetchone(self):
        sql, binds = self.last
        return (len(binds) if "BASIC_SHAREHOLDER_INFO" in sql else 1,)


def test_coverage_is_counted_in_chunks_of_ids():
    cursor = FakeCursor()

    assert load_script().shareholder_coverage(cursor, ["XX1", "XX2", "XX3"], chunk_size=2) == (
        3,
        3,
        2,
    )
    assert len(cursor.calls) == 4  # two chunks, two tables
    assert all("GITHUB_COMBINED" not in sql for sql, _ in cursor.calls)
    assert sorted(v for _, binds in cursor.calls[:1] for v in binds.values()) == ["XX1", "XX2"]
