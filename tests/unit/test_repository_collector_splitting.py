"""Periods with more than 1,000 search results are split, and every part is collected.

GitHub returns at most 1,000 results per query. Before this was fixed, the standard mode
skipped the first part after each split, and the star-range mode did not split at all but
stopped at 1,000 results.
"""

import json

import pytest

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
    one_second_period,
)


@pytest.fixture
def api():
    return FakeSearchApi()


@pytest.fixture
def collector(tmp_path, monkeypatch, api):
    return make_collector(tmp_path, monkeypatch, api)


def run_standard(collector):
    return collector.collect_repositories(
        start_date=START, end_date=END, min_stars=10, resume=False
    )


def run_star_range(collector):
    return collector.collect_repositories_by_star_range(
        start_date=START, end_date=END, min_stars=1, max_stars=5, resume=False
    )


def audit_lines(collector):
    return [json.loads(line) for line in collector.audit_file.read_text().splitlines()]


@pytest.mark.parametrize("run", [run_standard, run_star_range])
def test_all_parts_of_a_split_period_are_collected(collector, api, run):
    api.totals = {FULL: 1500, FIRST_HALF: 600, SECOND_HALF: 600}

    assert run(collector) == 1200

    assert sum(collector.stored) == 1200
    assert api.processed_periods() == [FULL, FIRST_HALF, SECOND_HALF]


def test_split_into_three_parts_collects_all_three(collector, api):
    api.totals = {FULL: 2500}
    api.default_total = 700

    assert run_standard(collector) == 3 * 700

    assert len(api.processed_periods()) == 1 + 3


def test_part_that_is_still_too_large_is_split_again(collector, api):
    api.totals = {FULL: 1500, FIRST_HALF: 1500, SECOND_HALF: 600}
    api.default_total = 600

    # the first half is split into two quarters of 600 each
    assert run_standard(collector) == 3 * 600


def test_nothing_of_a_period_is_stored_before_it_is_split(collector, api):
    api.totals = {FULL: 1500, FIRST_HALF: 600, SECOND_HALF: 600}

    run_standard(collector)

    # the 100 items on the first page of the oversized period are not stored twice
    assert sum(collector.stored) == 1200


def test_star_range_mode_keeps_its_star_bounds_in_every_part(collector, api):
    api.totals = {FULL: 1500, FIRST_HALF: 600, SECOND_HALF: 600}

    run_star_range(collector)

    assert all("stars:1..5" in query for query in api.queries)


def test_split_check_costs_no_extra_request(collector, api):
    api.totals = {FULL: 250}

    assert run_standard(collector) == 250

    # 100 + 100 + 50: the third page is shorter than 100 and ends the period
    assert api.requests == [(FULL, 1), (FULL, 2), (FULL, 3)]


def test_period_of_one_second_is_truncated_and_recorded(collector, api):
    start, end = one_second_period()
    api.totals = {key(start, end): 1500}
    collector.state.set_time_periods([(start, end)])

    assert collector._collect_repositories_in_period(start, end, min_stars=10) == 1000

    truncated = collector.state.state["truncated_periods"]
    assert truncated == [{"start": start.isoformat(), "end": end.isoformat(), "total_count": 1500}]
    assert audit_lines(collector)[-1]["truncated"] is True


def test_resume_in_the_middle_of_a_period_continues_at_the_saved_page(collector, api):
    api.totals = {FULL: 900}
    collector.state.set_time_periods([(START, END)])
    collector.state.update(current_period_page=4)

    assert collector._collect_repositories_in_period(START, END, min_stars=10) == 600

    assert api.requests[0] == (FULL, 4)
    assert audit_lines(collector)[-1]["first_page"] == 4


def test_incomplete_response_is_recorded_and_collection_continues(collector, api, caplog):
    api.totals = {FULL: 40}
    api.incomplete = {FULL}
    collector.state.set_time_periods([(START, END)])

    assert collector._collect_repositories_in_period(START, END, min_stars=10) == 40

    assert collector.state.state["incomplete_responses"] == [
        {"start": START.isoformat(), "end": END.isoformat(), "page": 1}
    ]
    assert "incomplete_results" in caplog.text
    assert audit_lines(collector)[-1]["incomplete_pages"] == 1


def test_each_finished_period_gets_an_audit_line(collector, api):
    api.totals = {FULL: 1500, FIRST_HALF: 600, SECOND_HALF: 550}

    run_standard(collector)

    lines = audit_lines(collector)
    assert [(line["start"], line["end"]) for line in lines] == [
        (START.isoformat(), MID.isoformat()),
        (MID.isoformat(), END.isoformat()),
    ]
    assert [(line["total_count"], line["collected"], line["shortfall"]) for line in lines] == [
        (600, 600, 0),
        (550, 550, 0),
    ]
    assert all(line["min_stars"] == 10 and line["max_stars"] is None for line in lines)


def test_audit_line_shows_a_shortfall_when_fewer_items_arrive_than_announced(collector, api):
    api.totals = {FULL: 300}
    original = api.search_repositories

    def short_second_page(query, sort, order, per_page, page):
        result = original(query, sort, order, per_page, page)
        if page == 2:
            result["items"] = result["items"][:30]
        return result

    api.search_repositories = short_second_page
    collector.state.set_time_periods([(START, END)])

    assert collector._collect_repositories_in_period(START, END, min_stars=10) == 130

    line = audit_lines(collector)[-1]
    assert (line["total_count"], line["collected"], line["shortfall"]) == (300, 130, 170)
