"""Tests for scripts/maintenance/reset_stale_countries.py (K3).

Temporary SQLite database and cache file; no geocoding request is sent.
"""

import csv
import importlib.util
from pathlib import Path

import pytest

from github_collector.database.database import session_scope
from github_collector.database.engine import make_engine
from github_collector.database.models import Contributor, Organization
from github_collector.geocoding.cache import GeocodingCache
from github_collector.geocoding.heuristics import HeuristicResolver

SCRIPT = (
    Path(__file__).parent.parent.parent / "scripts" / "maintenance" / "reset_stale_countries.py"
)


@pytest.fixture
def module(monkeypatch, test_db):
    spec = importlib.util.spec_from_file_location("reset_stale_under_test", SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    monkeypatch.setattr(loaded.config, "DB_URL", test_db.db_url)

    def sqlite_only_engine(db_url=None):
        engine = make_engine(db_url)
        assert engine.dialect.name == "sqlite", f"test tried to open {engine.dialect.name}"
        return engine

    monkeypatch.setattr(loaded, "make_engine", sqlite_only_engine)
    return loaded


@pytest.fixture
def cache_file(tmp_path):
    cache = GeocodingCache(tmp_path / "cache.json")
    cache.set("Paris", {"country_code": "FR", "country": "France", "region": None})
    cache.set("Berlin", {"country_code": "DE", "country": "Germany", "region": None})
    cache.set("Nowhere Land", {"country_code": "", "country": None})
    cache.set("Korea, South", {"country_code": "AU", "country": "Australia", "region": "NSW"})
    cache.save()
    return tmp_path / "cache.json"


@pytest.fixture
def rows(test_db):
    people = [
        (1, "Paris", "DE"),  # cache says FR -> different
        (2, "Xqzv Plomb", "GB"),  # never geocoded, no rule -> not_in_cache
        (3, "Berlin", "DE"),  # unchanged
        (4, "Nowhere Land", "NL"),  # cache explicitly has no country -> untouched
        (5, "Korea, South", "AU"),  # mapping names KR and outranks the cache -> different
        (6, "Lyon, FR", "FR"),  # not cached, text rule gives FR -> unchanged
        (7, "Georgia", "GE"),  # bare "Georgia" counts as the US state (E9) -> different
    ]
    with session_scope(test_db.session_factory) as session:
        for row_id, location, code in people:
            session.add(
                Contributor(
                    id=row_id,
                    login=f"u{row_id}",
                    type="User",
                    location=location,
                    country_code=code,
                    region="r",
                )
            )
        session.add(Organization(id=20, login="org", location="Paris", country_code="DE"))
    return test_db


def _countries(test_db, model):
    with session_scope(test_db.session_factory) as session:
        return {r.id: r.country_code for r in session.query(model)}


def test_find_stale_groups_rows(module, rows, cache_file, test_db):
    engine = make_engine(test_db.db_url)
    with engine.connect() as conn:
        stale = module.find_stale(
            conn, "contributors", GeocodingCache(cache_file), HeuristicResolver()
        )
    assert sorted(stale["different"]) == [1, 5, 7]
    assert stale["not_in_cache"] == [2]


def test_dry_run_changes_nothing(module, rows, cache_file, test_db, tmp_path):
    before = _countries(test_db, Contributor)
    assert module.main(["--cache-file", str(cache_file), "--backup-dir", str(tmp_path)]) == 0
    assert _countries(test_db, Contributor) == before
    assert not list(tmp_path.glob("*.csv"))


def test_write_resets_both_groups_and_backs_them_up(module, rows, cache_file, test_db, tmp_path):
    args = ["--cache-file", str(cache_file), "--backup-dir", str(tmp_path), "--write"]
    assert module.main(args) == 0
    countries = _countries(test_db, Contributor)
    assert {k: countries[k] for k in (1, 2, 5, 7)} == {1: None, 2: None, 5: None, 7: None}
    assert (countries[3], countries[4], countries[6]) == ("DE", "NL", "FR")
    assert _countries(test_db, Organization)[20] is None

    backup = tmp_path / next(p.name for p in tmp_path.glob("*contributors*.csv"))
    with backup.open(encoding="utf-8", newline="") as f:
        saved = {int(r["id"]): r for r in csv.DictReader(f)}
    assert set(saved) == {1, 2, 5, 7}
    assert saved[1]["country_code"] == "DE" and saved[1]["location"] == "Paris"
    assert saved[1]["region"] == "r"
