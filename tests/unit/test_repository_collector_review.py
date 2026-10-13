"""Regression tests for defects found in the independent review of 2026-10-13.

Each of them let a period count as collected although repositories were missing, or made a
run do something other than what its arguments said.
"""

import json
from contextlib import contextmanager

import pytest
import requests

import github_collector.repository_collector as rc
from github_collector.api.github_api import GitHubAPIError
from github_collector.repository_collector import CollectionStateMismatchError
from tests.unit.collector_fakes import (
    END,
    FIRST_HALF,
    FULL,
    MID,
    SECOND_HALF,
    START,
    FakeSearchApi,
    key,
    make_collector,
)


@pytest.fixture
def api():
    return FakeSearchApi()


@pytest.fixture
def collector(tmp_path, monkeypatch, api):
    return make_collector(tmp_path, monkeypatch, api)


def audit_lines(collector):
    return [json.loads(line) for line in collector.audit_file.read_text().splitlines()]


def standard(collector, **kwargs):
    kwargs.setdefault("min_stars", 10)
    return collector.collect_repositories(start_date=START, end_date=END, **kwargs)


def star_range(collector, min_stars, max_stars, **kwargs):
    return collector.collect_repositories_by_star_range(
        start_date=START, end_date=END, min_stars=min_stars, max_stars=max_stars, **kwargs
    )


# --- storing ------------------------------------------------------------------------------


def test_database_error_while_storing_is_not_swallowed(collector, monkeypatch):
    @contextmanager
    def no_session(session_factory=None):
        yield object()

    monkeypatch.setattr(rc, "session_scope", no_session)
    collector.db.bulk_upsert_contributors.side_effect = RuntimeError("MySQL server has gone away")

    with pytest.raises(RuntimeError):
        collector._persist_batch_to_database([{"id": 1}], [{"login": "a"}], [], "abc123")


def test_audit_line_separates_fetched_from_stored(collector, api):
    api.totals = {FULL: 250}
    collector._process_repositories_batch = lambda items: len(items) - 10
    collector.state.set_time_periods([(START, END)])

    collector._collect_repositories_in_period(START, END, min_stars=10)

    line = audit_lines(collector)[-1]
    assert (line["collected"], line["stored"], line["shortfall"]) == (250, 220, 0)


# --- limit and resume ---------------------------------------------------------------------


def test_limit_does_not_make_a_later_run_skip_the_rest_of_the_period(collector, api):
    api.totals = {FULL: 500}

    standard(collector, max_repos=120, resume=False)
    standard(collector, max_repos=120)
    # the second run must leave the period open instead of ending it after one page
    assert collector.state.state.get("finished") is False
    standard(collector)

    # five full pages hold the 500 repositories; the sixth request returns the empty page
    pages = sorted({page for period, page in api.requests if period == FULL})
    assert pages == [1, 2, 3, 4, 5, 6]
    assert sum(collector.stored) >= 500
    # the period was continued, not started again
    assert api.requests.count((FULL, 1)) == 1
    assert collector.state.state.get("finished") is True


# --- state belongs to one collection ------------------------------------------------------


def test_new_star_range_after_a_finished_run_collects_all_periods(collector, api, monkeypatch):
    monkeypatch.setitem(rc.BATCH_CONFIG, "repository_initial_period_days", 15)
    api.default_total = 50

    star_range(collector, 1, 5)
    first_run = len(api.requests)
    star_range(collector, 6, 10)

    second = api.queries[first_run:]
    assert len(second) == first_run == 2
    assert all("stars:6..10" in query for query in second)
    assert {query.split()[0] for query in second} == {
        f"created:{FIRST_HALF}",
        f"created:{SECOND_HALF}",
    }


def test_unfinished_run_with_other_arguments_is_refused(collector, api):
    api.totals = {FULL: 500}
    star_range(collector, 1, 5, max_repos=100, resume=False)

    with pytest.raises(CollectionStateMismatchError) as error:
        star_range(collector, 6, 10)

    assert "1..5" in str(error.value)
    assert collector.state.state["current_period_page"] == 1  # untouched


def test_unfinished_run_with_the_same_arguments_is_resumed(collector, api):
    api.totals = {FULL: 300}
    star_range(collector, 1, 5, max_repos=100, resume=False)

    assert star_range(collector, 1, 5) == 300  # page 1 is fetched again, then 2 and 3

    assert collector.state.state["finished"] is True


def test_state_file_of_an_older_version_is_refused(collector, api):
    collector.state.set_time_periods([(START, END)])  # no arguments recorded, as before
    api.totals = {FULL: 100}

    with pytest.raises(CollectionStateMismatchError):
        star_range(collector, 1, 5)


# --- configuration ------------------------------------------------------------------------


def test_page_size_other_than_100_still_reads_every_page(collector, api, monkeypatch):
    monkeypatch.setitem(rc.GITHUB_API, "search_page_size", 50)
    api.totals = {FULL: 300}
    collector.state.set_time_periods([(START, END)])

    assert collector._collect_repositories_in_period(START, END, min_stars=10) == 300
    assert [page for _period, page in api.requests] == [1, 2, 3, 4, 5, 6, 7]


def test_lower_result_limit_splits_instead_of_looping(collector, api, monkeypatch):
    monkeypatch.setitem(rc.GITHUB_API, "search_max_results", 500)
    api.totals = {FULL: 700, key(START, MID): 350, key(MID, END): 350}

    assert standard(collector, resume=False) == 700


# --- answers that are not answers ---------------------------------------------------------


def test_network_error_is_retried(collector, api):
    api.totals = {FULL: 40}
    api.errors = [requests.ConnectionError("connection reset")]
    collector.state.set_time_periods([(START, END)])

    assert collector._collect_repositories_in_period(START, END, min_stars=10) == 40


def test_response_without_a_total_is_an_error_not_an_empty_period(collector, api):
    api.search_repositories = lambda **kwargs: {}
    collector.state.set_time_periods([(START, END)])

    with pytest.raises(GitHubAPIError):
        collector._collect_repositories_in_period(START, END, min_stars=10)


# --- owner -------------------------------------------------------------------------------


def test_repository_of_an_organization_gets_its_organization_id(collector):
    repositories = [
        {
            "id": 1,
            "name": "a",
            "full_name": "acme/a",
            "owner": {"login": "acme", "type": "Organization"},
        },
        {"id": 2, "name": "b", "full_name": "alice/b", "owner": {"login": "alice", "type": "User"}},
    ]

    rows = collector._normalize_repositories_for_db(
        repositories, {"acme": 20, "alice": 1}, {"acme": 20}
    )

    assert [(row["owner_id"], row["organization_id"]) for row in rows] == [(20, 20), (1, None)]
