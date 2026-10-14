"""The methodology queries must be read-only and must run against the current schema."""

import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, event, text

from github_collector.database.database import session_scope
from github_collector.database.models import Contributor, Organization, Repository

SQL_FILE = (
    Path(__file__).parent.parent.parent / "scripts" / "diagnostics" / "methodology_checks.sql"
)


def statements():
    content = SQL_FILE.read_text(encoding="utf-8")
    without_comments = "\n".join(
        line for line in content.splitlines() if not line.strip().startswith("--")
    )
    return [s.strip() for s in without_comments.split(";") if s.strip()]


def test_every_statement_is_a_select():
    parsed = statements()

    assert len(parsed) == 9
    assert all(statement.upper().startswith("SELECT") for statement in parsed)
    forbidden = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE)\b", re.I)
    assert not any(forbidden.search(statement) for statement in parsed)


def test_repositories_is_read_in_full_at_most_twice():
    # The table has 27.5 million rows; each full read took minutes to hours. Only the
    # query over the index on collected_at may read it in addition.
    full_reads = [
        s
        for s in statements()
        if re.search(r"\bFROM repositories\b", s, re.I)
        and not re.search(r"WHERE\s+collected_at\b", s, re.I)
    ]
    assert len(full_reads) == 2


def _sqlite_date_functions(engine):
    """MySQL's YEAR() and MONTH() as SQLite functions, so the same file runs in the tests."""

    def part(index):
        def extract(value):
            return None if value is None else int(str(value)[:10].split("-")[index])

        return extract

    @event.listens_for(engine, "connect")
    def register(dbapi_connection, _record):
        dbapi_connection.create_function("YEAR", 1, part(0))
        dbapi_connection.create_function("MONTH", 1, part(1))


def test_statements_run_on_the_current_schema_and_apply_the_owner_rule(test_db):
    with session_scope(test_db.session_factory) as session:
        session.add_all(
            [
                Contributor(id=1, login="alice", type="User", location="Berlin", country_code="DE"),
                # placeholder row of the organization, with a different country
                Contributor(id=20, login="acme", type="Organization", country_code="US"),
                Organization(id=20, login="acme", location="Paris", country_code="FR"),
            ]
        )
        session.flush()
        session.add_all(
            [
                Repository(
                    id=10,
                    name="a",
                    full_name="alice/a",
                    owner_id=1,
                    stargazers_count=12,
                    created_at=datetime(2024, 3, 5),
                    collected_at=datetime(2025, 12, 1),
                    commits_count=4,
                    pull_requests_count=1,
                ),
                # as in older collections: owned by an organization, organization_id not set
                Repository(
                    id=11,
                    name="b",
                    full_name="acme/b",
                    owner_id=20,
                    stargazers_count=3,
                    created_at=datetime(2024, 3, 9),
                ),
            ]
        )

    engine = create_engine(test_db.db_url)
    _sqlite_date_functions(engine)
    with engine.connect() as conn:
        results = [conn.execute(text(statement)).fetchall() for statement in statements()]

    # query 1: one creation month with both repositories, one star class each, one
    # repository with statistics, one without retrieval time, no forks
    assert [tuple(r) for r in results[0]] == [(2024, 3, 2, 0, 1, 0, 1, 1, 1, 0)]
    # query 5: one organization with a placeholder row, the two rows disagree
    assert tuple(results[4][0])[:3] == (1, 0, 1)
    # query 6: both repositories get a country; the organization's repository from the
    # organizations table although organization_id is empty; no owner row missing
    assert sorted(tuple(row) for row in results[5]) == [
        ("organization", 1, 1, 0, 0, 0),
        ("user", 1, 1, 0, 0, 0),
    ]
