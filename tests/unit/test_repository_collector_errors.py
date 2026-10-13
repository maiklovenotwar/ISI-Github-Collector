"""A failed search must never look like an empty period.

Before this was fixed, the collector turned every API error into "0 repositories", marked
the period as done and moved on, which left gaps that only a log line revealed.
"""

import pytest

from github_collector.api.github_api import (
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubRateLimitError,
)
from tests.unit.collector_fakes import END, FULL, MID, START, FakeSearchApi, make_collector


@pytest.fixture
def api():
    return FakeSearchApi()


@pytest.fixture
def collector(tmp_path, monkeypatch, api):
    return make_collector(tmp_path, monkeypatch, api)


@pytest.mark.parametrize("error", [GitHubAPIError("HTTP 500"), GitHubRateLimitError("limit")])
def test_persistent_search_error_is_raised_after_three_attempts(collector, api, error):
    api.always_fail = error

    with pytest.raises(type(error)):
        collector._collect_repositories_in_period(START, END, min_stars=10)

    assert api.requests == [(FULL, 1)] * 3


def test_invalid_tokens_abort_immediately(collector, api):
    api.always_fail = GitHubAuthenticationError("401")

    with pytest.raises(GitHubAuthenticationError):
        collector._collect_repositories_in_period(START, END, min_stars=10)

    assert api.requests == [(FULL, 1)]


def test_failed_period_is_not_marked_as_done(collector, api):
    api.always_fail = GitHubAPIError("HTTP 500")

    with pytest.raises(GitHubAPIError):
        collector.collect_repositories(start_date=START, end_date=END, min_stars=10, resume=False)

    assert collector.state.state["current_period_index"] == 0
    assert collector.state.get_current_period() == (START, END)


def test_error_in_the_middle_of_a_period_keeps_the_page_for_resuming(collector, api):
    api.totals = {FULL: 500}
    collector.state.set_time_periods([(START, END)])
    original = api.search_repositories

    def fail_on_page_three(query, sort, order, per_page, page):
        if page == 3:
            api.requests.append((FULL, page))
            raise GitHubAPIError("HTTP 502")
        return original(query, sort, order, per_page, page)

    api.search_repositories = fail_on_page_three

    with pytest.raises(GitHubAPIError):
        collector._collect_repositories_in_period(START, END, min_stars=10)

    assert sum(collector.stored) == 200
    assert collector.state.get("current_period_page") == 3


def test_transient_error_is_retried_and_then_succeeds(collector, api):
    api.totals = {FULL: 40}
    api.errors = [GitHubAPIError("HTTP 502")]

    assert collector._collect_repositories_in_period(START, END, min_stars=10) == 40


def test_star_range_mode_retries_and_raises_like_the_standard_mode(collector, api):
    api.always_fail = GitHubAPIError("HTTP 500")

    with pytest.raises(GitHubAPIError):
        collector._collect_repositories_in_period_by_star_range(START, MID, 1, 5)

    assert len(api.requests) == 3
    assert "stars:1..5" in api.queries[0]


def test_volume_estimate_reports_zero_on_error_without_raising(tmp_path, monkeypatch, api):
    api.always_fail = GitHubAPIError("HTTP 500")
    collector = make_collector(tmp_path, monkeypatch, api)
    del collector._get_total_repositories_in_timeframe  # use the real method again

    assert collector._get_total_repositories_in_timeframe(START, END) == 0
