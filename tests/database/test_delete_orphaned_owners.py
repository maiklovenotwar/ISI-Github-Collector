"""Tests for GitHubDatabase.delete_orphaned_owners.

The ``contributors`` table holds one row per GitHub account, irrespective of its
role. An account that owns no collected repository is still needed when it is
referenced by ``repository_contributors`` (the per-repository contributor
network), and must therefore not be treated as orphaned.

Organizations are orphaned when no repository refers to them. Repositories of user
accounts carry no organization and must not prevent that detection.
"""

from datetime import datetime, timezone

import pytest

from github_collector.database.database import session_scope
from github_collector.database.models import (
    Contributor,
    Organization,
    Repository,
    RepositoryContributor,
)

OWNER, COLLABORATOR, UNLINKED_COLLABORATOR, ORPHAN = 1, 2, 3, 4


def _link(repository_id: int, login: str, **fields) -> RepositoryContributor:
    return RepositoryContributor(
        repository_id=repository_id,
        github_login=login,
        collected_at=datetime.now(timezone.utc),
        **fields,
    )


@pytest.fixture
def populated_db(test_db):
    """One repository with an owner, two collaborators and an anonymous author, plus an orphan."""
    with session_scope(test_db.session_factory) as session:
        session.add_all(
            [
                Contributor(id=OWNER, login="owner", type="User"),
                Contributor(id=COLLABORATOR, login="collaborator", type="User"),
                Contributor(id=UNLINKED_COLLABORATOR, login="unlinked", type="User"),
                Contributor(id=ORPHAN, login="orphan", type="User"),
            ]
        )
        session.flush()
        session.add(Repository(id=10, name="repo", full_name="owner/repo", owner_id=OWNER))
        session.flush()
        session.add_all(
            [
                _link(10, "collaborator", contributor_id=COLLABORATOR, github_user_id=COLLABORATOR),
                # Collected, but contributor_id has not been backfilled yet.
                _link(10, "unlinked", github_user_id=UNLINKED_COLLABORATOR),
                # Anonymous commit author: neither id is set.
                _link(10, "anonymous@example.org", is_anonymous=True),
            ]
        )
    return test_db


def _contributor_ids(db) -> set:
    with session_scope(db.session_factory) as session:
        return {row.id for row in session.query(Contributor.id)}


def test_only_unreferenced_contributors_are_removed(populated_db):
    removed_contributors, _ = populated_db.delete_orphaned_owners()

    assert removed_contributors == 1
    assert _contributor_ids(populated_db) == {OWNER, COLLABORATOR, UNLINKED_COLLABORATOR}


def test_collaborator_links_survive_the_cleanup(populated_db):
    populated_db.delete_orphaned_owners()

    with session_scope(populated_db.session_factory) as session:
        link = session.query(RepositoryContributor).filter_by(github_login="collaborator").one()
        assert link.contributor_id == COLLABORATOR


def test_dry_run_removes_nothing(populated_db):
    removed_contributors, removed_organizations = populated_db.delete_orphaned_owners(dry_run=True)

    assert (removed_contributors, removed_organizations) == (0, 0)
    assert _contributor_ids(populated_db) == {OWNER, COLLABORATOR, UNLINKED_COLLABORATOR, ORPHAN}


def test_contributor_without_any_reference_is_removed_from_an_empty_network(test_db):
    with session_scope(test_db.session_factory) as session:
        session.add_all(
            [
                Contributor(id=OWNER, login="owner", type="User"),
                Contributor(id=ORPHAN, login="orphan", type="User"),
            ]
        )
        session.flush()
        session.add(Repository(id=10, name="repo", full_name="owner/repo", owner_id=OWNER))

    removed_contributors, _ = test_db.delete_orphaned_owners()

    assert removed_contributors == 1
    assert _contributor_ids(test_db) == {OWNER}


def _organization_logins(db) -> set:
    with session_scope(db.session_factory) as session:
        return {row.login for row in session.query(Organization.login)}


@pytest.fixture
def db_with_organizations(test_db):
    """One organization with a repository, one without, and a repository owned by a user."""
    with session_scope(test_db.session_factory) as session:
        session.add(Contributor(id=OWNER, login="owner", type="User"))
        session.add_all(
            [Organization(id=20, login="active-org"), Organization(id=21, login="orphaned-org")]
        )
        session.flush()
        session.add_all(
            [
                Repository(
                    id=10, name="a", full_name="active-org/a", owner_id=OWNER, organization_id=20
                ),
                # A repository of a user account has no organization.
                Repository(id=11, name="b", full_name="owner/b", owner_id=OWNER),
            ]
        )
    return test_db


def test_organization_without_repository_is_removed(db_with_organizations):
    _, removed_organizations = db_with_organizations.delete_orphaned_owners()

    assert removed_organizations == 1
    assert _organization_logins(db_with_organizations) == {"active-org"}


def test_dry_run_reports_orphaned_organizations_without_removing_them(db_with_organizations):
    _, removed_organizations = db_with_organizations.delete_orphaned_owners(dry_run=True)

    assert removed_organizations == 0
    assert _organization_logins(db_with_organizations) == {"active-org", "orphaned-org"}
