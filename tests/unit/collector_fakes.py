"""Stand-ins for the GitHub search API, shared by the collector tests.

The fake answers `search/repositories` the way the collector sees it: a total count per
creation period and pages of at most `per_page` items, never more than 1,000 items per
query. Nothing is sent over the network.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import github_collector.repository_collector as rc
from github_collector.repository_collector import RepositoryCollector

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime(2024, 1, 31, tzinfo=timezone.utc)
MID = START + (END - START) / 2

Period = Tuple[datetime, datetime]


def key(start: datetime, end: datetime) -> str:
    """The creation range as it appears in a search query."""
    return f"{start.isoformat()}..{end.isoformat()}"


FULL = key(START, END)
FIRST_HALF = key(START, MID)
SECOND_HALF = key(MID, END)


class FakeSearchApi:
    """Records every search request and answers from a table of totals."""

    def __init__(self) -> None:
        self.totals: Dict[str, int] = {}
        self.default_total = 0
        self.incomplete: set = set()
        self.errors: List[Exception] = []  # raised one per request, in order
        self.always_fail: Optional[Exception] = None
        self.requests: List[Tuple[str, int]] = []
        self.queries: List[str] = []

    def search_repositories(self, query, sort, order, per_page, page) -> Dict[str, Any]:
        period = query.split()[0][len("created:") :]
        self.requests.append((period, page))
        self.queries.append(query)
        if self.always_fail is not None:
            raise self.always_fail
        if self.errors:
            raise self.errors.pop(0)
        total = self.totals.get(period, self.default_total)
        offset = (page - 1) * per_page
        available = min(total, 1000)  # GitHub returns at most 1,000 results per query
        count = max(0, min(per_page, available - offset))
        return {
            "total_count": total,
            "incomplete_results": period in self.incomplete,
            "items": [{"id": offset + i} for i in range(count)],
        }

    def monitor_rate_limit(self) -> Dict[str, Any]:
        return {}

    def processed_periods(self) -> List[str]:
        """Periods of which at least one page was requested, in order, without repeats."""
        seen: List[str] = []
        for period, _page in self.requests:
            if period not in seen:
                seen.append(period)
        return seen


def make_collector(tmp_path, monkeypatch, api: FakeSearchApi) -> RepositoryCollector:
    """A real collector on a temporary state file; storing a page only counts its items."""
    monkeypatch.setattr(rc.time, "sleep", lambda seconds: None)
    collector = RepositoryCollector(
        github_client=api, db=MagicMock(name="GitHubDatabase"), state_file=str(tmp_path / "s.json")
    )
    collector.audit_file = tmp_path / "audit.jsonl"
    collector.stored: List[int] = []
    collector._process_repositories_batch = lambda items: collector.stored.append(len(items))
    collector.process_repositories_parallel = lambda items: collector.stored.append(len(items))
    # The volume estimate shown before a run costs one search request; keep it out of the counts.
    collector._get_total_repositories_in_timeframe = lambda *args, **kwargs: 0
    return collector


def one_second_period() -> Period:
    return START, START + timedelta(seconds=1)
