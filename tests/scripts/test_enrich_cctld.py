"""Validation of scripts/enrichment/enrich_cctld.py reports agreement per country."""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "enrichment" / "enrich_cctld.py"


@pytest.fixture(scope="module")
def module():
    spec = importlib.util.spec_from_file_location("enrich_cctld_under_test", SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'cctld.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE contributors (id INTEGER PRIMARY KEY, blog TEXT, email TEXT, "
                "country_code TEXT)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO contributors VALUES "
                "(1, 'https://linktr.ee/a', NULL, 'US'), "
                "(2, 'https://linktr.ee/b', NULL, 'IN'), "
                "(3, 'https://ria.ee', NULL, 'EE'), "
                "(4, 'https://firma.de', NULL, 'DE'), "
                "(5, NULL, 'x@uni.de', 'AT')"
            )
        )
    return engine


def test_linktree_accounts_are_not_evaluated(module, engine):
    with engine.connect() as conn:
        result = module.run_validation(conn, module.ENTITIES["contributors"])
    assert result["evaluated"] == 3
    assert result["no_tld"] == 2


def test_validation_reports_agreement_per_country(module, engine):
    with engine.connect() as conn:
        result = module.run_validation(conn, module.ENTITIES["contributors"])
    assert result["by_country"] == {"EE": (1, 1), "DE": (1, 2)}
