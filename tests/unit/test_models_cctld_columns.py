from sqlalchemy import DateTime, String

from github_collector.database.models import Contributor, Organization


def test_organization_has_cctld_columns_matching_migration():
    cols = Organization.__table__.columns
    assert isinstance(cols["country_code_cctld"].type, String)
    assert cols["country_code_cctld"].type.length == 2
    assert isinstance(cols["cctld_source"].type, String)
    assert cols["cctld_source"].type.length == 20
    assert isinstance(cols["cctld_enriched_at"].type, DateTime)
    assert isinstance(cols["cctld_run_id"].type, String)
    assert cols["cctld_run_id"].type.length == 36
    assert cols["cctld_run_id"].index is True  # matches idx_organizations_cctld_run_id


def test_contributor_has_cctld_columns_matching_migration():
    cols = Contributor.__table__.columns
    assert isinstance(cols["country_code_cctld"].type, String)
    assert cols["country_code_cctld"].type.length == 2
    assert isinstance(cols["cctld_source"].type, String)
    assert cols["cctld_source"].type.length == 20
    assert isinstance(cols["cctld_enriched_at"].type, DateTime)
    assert isinstance(cols["cctld_run_id"].type, String)
    assert cols["cctld_run_id"].type.length == 36
    # add_cctld_contributors_no_index.py deliberately adds no index here
    assert cols["cctld_run_id"].index is not True
