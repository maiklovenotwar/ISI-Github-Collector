"""The methodology queries must be read-only and must run against the current schema."""

import re
from pathlib import Path

from sqlalchemy import create_engine, text

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

    assert len(parsed) == 11
    assert all(statement.upper().startswith("SELECT") for statement in parsed)
    forbidden = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE)\b", re.I)
    assert not any(forbidden.search(statement) for statement in parsed)


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
                Repository(id=10, name="a", full_name="alice/a", owner_id=1),
                Repository(id=11, name="b", full_name="acme/b", owner_id=20, organization_id=20),
            ]
        )

    engine = create_engine(test_db.db_url)
    with engine.connect() as conn:
        results = [conn.execute(text(statement)).fetchall() for statement in statements()]

    # query 7: one organization with a placeholder row, the two rows disagree
    assert tuple(results[6][0])[:3] == (1, 0, 1)
    # query 8: both repositories get a country, the organization's repository from organizations
    assert sorted(tuple(row) for row in results[7]) == [("organization", 1, 1), ("user", 1, 1)]
