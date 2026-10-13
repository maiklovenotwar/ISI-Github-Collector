"""Tests for scripts/maintenance/recheck_ambiguous_locations.py.

Temporary SQLite database; the geocoder is replaced, so no request is sent.
"""

import importlib.util
from pathlib import Path

import pytest

from github_collector.database.database import session_scope
from github_collector.database.engine import make_engine
from github_collector.database.models import Contributor, Organization
from github_collector.geocoding.geocoding_service import GeocodingService
from github_collector.geocoding.nominatim_client import GeocodingUnavailableError

SCRIPT = (
    Path(__file__).parent.parent.parent
    / "scripts"
    / "maintenance"
    / "recheck_ambiguous_locations.py"
)


class FakeClient:
    """Answers like the geocoder would for the places used below."""

    ANSWERS = {
        "Tbilisi, Georgia": "ge",
        "Perth, WA": "au",
        "Toronto, CA": "ca",
        "Fresno, CA": "us",
    }

    def __init__(self):
        self.asked = []
        self.down = set()

    def geocode(self, location):
        self.asked.append(location)
        if location in self.down:
            raise GeocodingUnavailableError("timeout")
        code = self.ANSWERS.get(location)
        return {"country_code": code, "country": code, "address": {}} if code else None


@pytest.fixture
def module(monkeypatch, test_db):
    spec = importlib.util.spec_from_file_location("recheck_ambiguous_under_test", SCRIPT)
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
def service(tmp_path):
    svc = GeocodingService(cache_file=str(tmp_path / "cache.json"))
    svc.nominatim_client = FakeClient()
    svc.rate_limiter.wait_for_api = lambda: None
    # the cache holds the results of the old rule
    for location in ("Tbilisi, Georgia", "Perth, WA", "Toronto, CA", "Fresno, CA"):
        svc.cache.set(location, {"country_code": "US"})
    return svc


@pytest.fixture
def rows(test_db):
    with session_scope(test_db.session_factory) as session:
        session.add_all(
            [
                Contributor(id=1, login="a", location="Tbilisi, Georgia", country_code="US"),
                Contributor(id=2, login="b", location="Tbilisi, Georgia", country_code="US"),
                Contributor(id=3, login="c", location="Atlanta, Georgia", country_code="US"),
                Contributor(id=4, login="d", location="Perth, WA", country_code="US"),
                Contributor(id=5, login="e", location="Fresno, CA", country_code="US"),
                Contributor(id=6, login="f", location="Somewhere odd, CA", country_code="US"),
                Contributor(id=7, login="g", location="Berlin", country_code="DE"),
                Organization(id=20, login="org", location="Toronto, CA", country_code="US"),
            ]
        )
    return test_db


def countries(db):
    with session_scope(db.session_factory) as session:
        result = {row.login: row.country_code for row in session.query(Contributor)}
        result.update({row.login: row.country_code for row in session.query(Organization)})
        return result


BEFORE = {"a": "US", "b": "US", "c": "US", "d": "US", "e": "US", "f": "US", "g": "DE", "org": "US"}


def test_dry_run_reports_and_changes_nothing(module, rows, service, capsys):
    assert module.main([], service=service) == 0

    assert countries(rows) == BEFORE
    out = capsys.readouterr().out
    assert "PROBELAUF" in out
    assert "US -> GE: 2" in out and "US -> AU: 1" in out and "US -> CA: 1" in out


def test_write_changes_only_rows_whose_country_differs(module, rows, service):
    assert module.main(["--write"], service=service) == 0

    assert countries(rows) == {
        "a": "GE",
        "b": "GE",
        "c": "US",  # known US city before the state: decided by the rule
        "d": "AU",
        "e": "US",  # the geocoder confirms the United States
        "f": "US",  # no result: the stored country is kept
        "g": "DE",  # not selected
        "org": "CA",
    }


def test_known_us_places_are_not_sent_to_the_geocoder(module, rows, service):
    module.main([], service=service)

    assert "Atlanta, Georgia" not in service.nominatim_client.asked
    assert service.nominatim_client.asked.count("Tbilisi, Georgia") == 1  # asked once for two rows


def test_unanswered_location_keeps_its_country_and_sets_the_exit_code(module, rows, service):
    service.nominatim_client.down = {"Perth, WA"}

    assert module.main(["--write"], service=service) == 1

    assert countries(rows)["d"] == "US"
    assert countries(rows)["a"] == "GE"


def test_limit_bounds_the_number_of_locations(module, rows, service):
    module.main(["--limit", "2"], service=service)

    assert len(service.nominatim_client.asked) <= 2
