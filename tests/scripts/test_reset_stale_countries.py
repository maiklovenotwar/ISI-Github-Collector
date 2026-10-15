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
    # a wrong match of the kind found in K12
    geocoder = {"latitude": 1.0, "longitude": 1.0}  # geocoder matches carry coordinates
    cache.set(
        "Xqzv Area", {"country_code": "CO", "country": "Colombia", "region": None, **geocoder}
    )
    # Nominatim reports Hong Kong under China (E15)
    cache.set(
        "Kowloon Tong",
        {"country_code": "CN", "country": "China", "region": "Kowloon", **geocoder},
    )
    # wrong matches corrected by the table of corrections (E14)
    cache.set("Antarctica", {"country_code": "CA", "country": "Canada", "region": None})
    cache.set("Istanbul/Turkey", {"country_code": "IT", "country": "Italy", "region": None})
    # written by earlier text rules (no coordinates); the current rules decide differently
    cache.set("Montreal, CA", {"country_code": "US", "country": "United States", "latitude": None})
    cache.set("Boise, ID", {"country_code": "ID", "country": "Indonesia", "latitude": None})
    cache.set("Korea, South", {"country_code": "AU", "country": "Australia", "region": "NSW"})
    cache.set(
        "Remote, Korea, South", {"country_code": "AU", "country": "Australia", "region": "NSW"}
    )
    cache.save()
    return tmp_path / "cache.json"


@pytest.fixture
def rows(test_db):
    people = [
        (1, "Xqzv Area", "US"),  # cache says CO -> cache (not written, K12)
        (2, "Xqzv Plomb", "GB"),  # never geocoded, no rule -> not_in_cache
        (3, "Berlin", "DE"),  # unchanged
        (4, "Nowhere Land", "NL"),  # cache explicitly has no country -> untouched
        (5, "Korea, South", "AU"),  # mapping names KR and outranks the cache -> mapping
        (6, "Lyon, FR", "FR"),  # not cached, text rule gives FR -> unchanged
        (7, "Georgia", "GE"),  # bare "Georgia" counts as the US state (E9) -> georgia
        (8, "Remote, Korea, South", "AU"),  # cleaned like geocode() does -> mapping KR
        (9, "Leeds, FR", "GB"),  # not cached, text rule gives FR -> rules (not written)
        (10, "Kowloon Tong", "CN"),  # cache CN, own ISO code HK -> territory
        (11, "Recife - PE", "PE"),  # the suffix rule no longer decides (K14) -> not_in_cache
        (12, "Antarctica", "CA"),  # correction: no country, outranks the cache -> mapping
        (13, "Istanbul/Turkey", "IT"),  # correction: TR, outranks the cache -> mapping
        (14, "Montreal, CA", "US"),  # old rule result, rule now gives CA -> rule_change
        (15, "Boise, ID", "ID"),  # old rule result, rule now leaves it open -> not_in_cache
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
        session.add(Organization(id=20, login="org", location="Xqzv Area", country_code="US"))
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
    assert stale == {
        "mapping": [5, 8, 12, 13],
        "georgia": [7],
        "territory": [10],
        "rule_change": [14],
        "cache": [1],
        "rules": [9],
        "not_in_cache": [2, 11, 15],
    }


def test_dry_run_changes_nothing(module, rows, cache_file, test_db, tmp_path):
    before = _countries(test_db, Contributor)
    assert module.main(["--cache-file", str(cache_file), "--backup-dir", str(tmp_path)]) == 0
    assert _countries(test_db, Contributor) == before
    assert not list(tmp_path.glob("*.csv"))


def test_write_resets_only_the_trusted_groups_and_backs_them_up(
    module, rows, cache_file, test_db, tmp_path
):
    # E11: the cache is often wrong itself (K12), and the text rules carry conventions that
    # are wrong for new places; their differences are reported, not written.
    args = ["--cache-file", str(cache_file), "--backup-dir", str(tmp_path), "--write"]
    assert module.main(args) == 0
    countries = _countries(test_db, Contributor)
    reset = (2, 5, 7, 8, 10, 11, 12, 13, 14, 15)
    assert {k: countries[k] for k in reset} == dict.fromkeys(reset)
    assert [countries[k] for k in (1, 3, 4, 6, 9)] == ["US", "DE", "NL", "FR", "GB"]
    assert _countries(test_db, Organization)[20] == "US"

    backup = tmp_path / next(p.name for p in tmp_path.glob("*contributors*.csv"))
    with backup.open(encoding="utf-8", newline="") as f:
        saved = {int(r["id"]): r for r in csv.DictReader(f)}
    assert set(saved) == set(reset)
    assert saved[5]["country_code"] == "AU" and saved[5]["location"] == "Korea, South"
    assert saved[5]["region"] == "r"
    assert not list(tmp_path.glob("*organizations*.csv"))


def test_write_is_refused_without_a_cache(module, rows, test_db, tmp_path):
    # A wrong path or an unreadable file gives an empty cache; every row would then count
    # as "never geocoded" and be reset.
    before = _countries(test_db, Contributor)
    args = ["--cache-file", str(tmp_path / "missing.json"), "--backup-dir", str(tmp_path)]
    assert module.main(args + ["--write"]) == 1
    assert _countries(test_db, Contributor) == before
    assert not list(tmp_path.glob("*.csv"))
