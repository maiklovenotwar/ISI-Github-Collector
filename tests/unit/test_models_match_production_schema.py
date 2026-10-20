"""The models build the same tables as the production database (functional test, F-8).

A rebuild with create_all reads only the models; whatever a migration created differently was
missing after the rebuild. Index names follow the production database.
"""

from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from github_collector.database.models import (
    Contributor,
    OrbisEnrichment,
    RepositoryContributor,
)


def index_names(model):
    return {index.name: [c.name for c in index.columns] for index in model.__table__.indexes}


def mysql_ddl(model):
    return str(CreateTable(model.__table__).compile(dialect=mysql.dialect()))


def test_contributor_cctld_country_is_indexed_as_in_production():
    assert index_names(Contributor)["idx_contributors_cctld"] == ["country_code_cctld"]


def test_rollback_marker_is_indexed_as_in_production():
    assert index_names(OrbisEnrichment)["idx_orbis_enrichments_reverted_at"] == ["reverted_at"]


def test_organization_link_has_no_extra_index_beside_the_unique_key():
    # uk_org_run (organization_id, run_id) already serves the foreign key.
    assert not any(cols == ["organization_id"] for cols in index_names(OrbisEnrichment).values())


def test_database_side_defaults_as_in_production():
    assert "blog_matched BOOL NOT NULL DEFAULT '0'" in mysql_ddl(OrbisEnrichment)
    assert "created_at DATETIME DEFAULT CURRENT_TIMESTAMP" in mysql_ddl(OrbisEnrichment)
    assert "is_anonymous BOOL NOT NULL DEFAULT '0'" in mysql_ddl(RepositoryContributor)
