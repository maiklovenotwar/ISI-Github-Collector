"""--recheck of scripts/enrichment/enrich_cctld.py recomputes stored ccTLD assignments."""

import importlib.util
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "enrichment" / "enrich_cctld.py"


@pytest.fixture(scope="module")
def module():
    spec = importlib.util.spec_from_file_location("enrich_cctld_recheck_under_test", SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'recheck.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE contributors (id INTEGER PRIMARY KEY, blog TEXT, email TEXT, "
                "country_code TEXT, country_code_cctld TEXT, cctld_source TEXT, "
                "cctld_enriched_at TEXT, cctld_run_id TEXT)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO contributors VALUES "
                "(1, 'https://linktr.ee/x', NULL, NULL, 'EE', 'blog', '2026-01-01', 'old'), "
                "(2, 'https://ria.ee', NULL, NULL, 'EE', 'blog', '2026-01-01', 'old'), "
                "(3, 'https://linktr.ee/y', 'a@uni-bremen.de', NULL, 'EE', 'blog', "
                "'2026-01-01', 'old'), "
                "(4, 'https://firma.de', NULL, 'DE', NULL, NULL, NULL, NULL)"
            )
        )
    return engine


def _rows(engine):
    with engine.connect() as conn:
        return {
            r[0]: tuple(r[1:])
            for r in conn.execute(
                text(
                    "SELECT id, country_code_cctld, cctld_source, cctld_run_id "
                    "FROM contributors ORDER BY id"
                )
            )
        }


def test_dry_run_reports_transitions_and_changes_nothing(module, engine):
    before = _rows(engine)
    with engine.connect() as conn:
        transitions = module.run_recheck(
            conn, module.ENTITIES["contributors"], dry_run=True, run_id="new"
        )
    assert transitions == {"EE -> -": 1, "EE -> DE": 1}
    assert _rows(engine) == before


def test_write_updates_only_changed_rows(module, engine):
    with engine.connect() as conn:
        module.run_recheck(conn, module.ENTITIES["contributors"], dry_run=False, run_id="new")
    rows = _rows(engine)
    assert rows[1] == (None, None, "new")
    assert rows[2] == ("EE", "blog", "old")
    assert rows[3] == ("DE", "email", "new")
    assert rows[4] == (None, None, None)


def test_recheck_without_write_is_a_dry_run(module, engine, monkeypatch):
    monkeypatch.setattr(module, "DB_URL", str(engine.url))
    monkeypatch.setattr(sys, "argv", ["enrich_cctld.py", "--entity", "contributors", "--recheck"])
    before = _rows(engine)
    assert module.main() == 0
    assert _rows(engine) == before
