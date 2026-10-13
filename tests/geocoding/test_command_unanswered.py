"""The geocoding command when the service does not answer, and its row selection.

A request the service did not answer must leave the row untouched, so that a later run
asks again. A row for which the service answered "no match" is marked with an empty
country code and is only selected again with `--retry-empty`.
"""

from __future__ import annotations

import pytest

from github_collector.cli import geocoding_command as cmd
from github_collector.database.database import GitHubDatabase
from github_collector.database.models import Contributor, Organization


class FakeGeocoder:
    """Answers from a table; `down` simulates an outage of the service."""

    def __init__(self, answers=None, down=False):
        self.answers = answers or {}
        self.down = down
        self.requested = []
        self.unavailable_count = 0
        self.last_request_unanswered = False

    def geocode(self, location):
        self.requested.append(location)
        if self.down:
            self.unavailable_count += 1
            self.last_request_unanswered = True
            return {}
        self.last_request_unanswered = False
        code = self.answers.get(location)
        return {"country_code": code, "country": code} if code else {}


@pytest.fixture()
def db() -> GitHubDatabase:
    return GitHubDatabase("sqlite:///:memory:")


def add(database, model, login, location, country_code=None):
    def _op(session):
        row = model(login=login, location=location, country_code=country_code)
        session.add(row)
        session.commit()
        return row.id

    return database._run_in_session(_op)


def country_codes(database, model):
    def _op(session):
        return {row.login: row.country_code for row in session.query(model)}

    return database._run_in_session(_op)


@pytest.mark.parametrize(
    "model, update",
    [(Contributor, cmd.update_contributors), (Organization, cmd.update_organizations)],
)
def test_unanswered_request_leaves_the_row_open(db, model, update):
    add(db, model, "a", "Xqzv Plomb")
    geocoder = FakeGeocoder(down=True)

    assert update(db, geocoder) == 0

    assert country_codes(db, model) == {"a": None}
    assert geocoder.requested == ["Xqzv Plomb"]  # asked once, no endless repetition


@pytest.mark.parametrize(
    "model, update",
    [(Contributor, cmd.update_contributors), (Organization, cmd.update_organizations)],
)
def test_answer_without_match_marks_the_row_as_processed(db, model, update):
    add(db, model, "a", "Xqzv Plomb")
    add(db, model, "b", "Karlsruhe xy")
    geocoder = FakeGeocoder(answers={"Karlsruhe xy": "DE"})

    assert update(db, geocoder) == 1

    assert country_codes(db, model) == {"a": "", "b": "DE"}


def test_run_stops_after_repeated_unanswered_requests(db):
    for i in range(40):
        add(db, Contributor, f"user{i}", f"Xqzv Plomb {i}")
    geocoder = FakeGeocoder(down=True)

    cmd.update_contributors(db, geocoder)

    assert len(geocoder.requested) == cmd.MAX_CONSECUTIVE_UNANSWERED
    assert set(country_codes(db, Contributor).values()) == {None}


def test_single_unanswered_requests_do_not_stop_the_run(db):
    for i in range(30):
        add(db, Contributor, f"user{i}", f"Ort {i}")
    geocoder = FakeGeocoder(answers={f"Ort {i}": "DE" for i in range(30)})
    original = geocoder.geocode

    def flaky(location):
        if location in ("Ort 3", "Ort 17"):
            geocoder.requested.append(location)
            geocoder.unavailable_count += 1
            geocoder.last_request_unanswered = True
            return {}
        return original(location)

    geocoder.geocode = flaky

    assert cmd.update_contributors(db, geocoder) == 28
    codes = country_codes(db, Contributor)
    assert codes["user3"] is None and codes["user17"] is None


def test_default_run_does_not_select_rows_marked_as_processed(db):
    add(db, Contributor, "a", "Xqzv Plomb", country_code="")
    geocoder = FakeGeocoder(answers={"Xqzv Plomb": "DE"})

    assert cmd.update_contributors(db, geocoder) == 0
    assert geocoder.requested == []


def test_retry_empty_selects_rows_marked_as_processed(db):
    add(db, Contributor, "a", "Xqzv Plomb", country_code="")
    add(db, Contributor, "b", "Still Unknown", country_code="")
    add(db, Contributor, "c", "Berlin xy", country_code="DE")
    geocoder = FakeGeocoder(answers={"Xqzv Plomb": "FR"})

    assert cmd.update_contributors(db, geocoder, retry_empty=True) == 1

    assert country_codes(db, Contributor) == {"a": "FR", "b": "", "c": "DE"}
    assert sorted(geocoder.requested) == ["Still Unknown", "Xqzv Plomb"]  # each once


def test_force_processes_every_row_once_and_ends(db):
    for i in range(60):
        add(db, Contributor, f"user{i}", f"Ort {i}", country_code="DE")
    geocoder = FakeGeocoder(answers={f"Ort {i}": "FR" for i in range(60)})

    assert cmd.update_contributors(db, geocoder, force=True) == 60

    assert len(geocoder.requested) == 60
    assert set(country_codes(db, Contributor).values()) == {"FR"}


def test_limit_still_bounds_the_number_of_updates(db):
    for i in range(60):
        add(db, Contributor, f"user{i}", f"Ort {i}")
    geocoder = FakeGeocoder(answers={f"Ort {i}": "DE" for i in range(60)})

    assert cmd.update_contributors(db, geocoder, limit=7) == 7


def test_exit_code_is_one_when_requests_stayed_unanswered():
    assert cmd.exit_code_for(FakeGeocoder()) == 0

    geocoder = FakeGeocoder()
    geocoder.unavailable_count = 3
    assert cmd.exit_code_for(geocoder) == 1
