"""Tests für den Firmen-Snapshot direkt auf orbis_enrichments.

Ersetzt eine frühere, separate `orbis_companies`-Cache-Tabelle (siehe
scripts/migrations/consolidate_orbis_enrichments.sql): die getrennte Tabelle
erforderte für "welche GH-Org gehört zu diesem Match, wie heißt die Firma?"
einen JOIN über zwei Tabellen und wusste selbst nichts über die GH-Seite.
Jetzt beantwortet eine einzelne Zeile in orbis_enrichments diese Frage
vollständig aus sich selbst heraus.
"""

from datetime import datetime

from github_collector.database.database import session_scope
from github_collector.database.models import OrbisEnrichment, Organization


def _make_org(test_db, org_id: int, login: str) -> Organization:
    return test_db.insert_organization(
        {"id": org_id, "login": login, "name": login, "created_at": datetime.now()}
    )


def test_enrichment_row_carries_full_company_snapshot(test_db) -> None:
    """Eine einzelne Zeile beantwortet 'wer wurde mit welcher Firma
    verknüpft, wie hieß sie?' ohne Join auf eine zweite Tabelle."""
    org = _make_org(test_db, 1, "BlinkTagInc")

    test_db.record_orbis_enrichment(
        organization_id=org.id,
        bvd_id="US312526703L",
        match_type="combined_medium",
        score=50,
        run_id="run-1",
        country_code="US",
        company_name="Blinktag Inc.",
        company_city="San Francisco",
        company_website="www.blinktag.com",
    )

    with session_scope(test_db.session_factory) as session:
        row = session.query(OrbisEnrichment).filter_by(organization_id=org.id).first()
        assert row.company_name == "Blinktag Inc."
        assert row.company_country == "US"
        assert row.company_city == "San Francisco"
        assert row.company_website == "www.blinktag.com"


def test_find_organization_from_bvd_id_via_single_table(test_db) -> None:
    """Genau der Anwendungsfall aus der Nutzerfrage: von einer BVDID direkt
    zur GH-Organisation UND zum Firmennamen, ohne zweite Tabelle."""
    org = _make_org(test_db, 1, "touchmedia360")
    test_db.record_orbis_enrichment(
        organization_id=org.id,
        bvd_id="RO36617326",
        match_type="combined_high",
        score=60,
        run_id="run-1",
        country_code="RO",
        company_name="Touch Media 360 Services SRL",
        company_city="Constanța",
    )

    with session_scope(test_db.session_factory) as session:
        row = (
            session.query(OrbisEnrichment, Organization)
            .join(Organization, Organization.id == OrbisEnrichment.organization_id)
            .filter(OrbisEnrichment.bvd_id == "RO36617326")
            .first()
        )
        enrichment, organization = row
        assert organization.login == "touchmedia360"
        assert enrichment.company_name == "Touch Media 360 Services SRL"


def test_company_snapshot_survives_rollback(test_db) -> None:
    """Der Firmen-Snapshot bleibt Teil der (nicht gelöschten) Logbuch-Zeile
    auch nach einem Rollback erhalten - dieselbe Zeile, dieselbe
    Information, nur `reverted_at` gesetzt."""
    org = _make_org(test_db, 1, "schlothauer-wauer")
    test_db.record_orbis_enrichment(
        organization_id=org.id,
        bvd_id="DE2011435329",
        match_type="combined_high",
        score=70,
        run_id="run-1",
        country_code="DE",
        company_name="Schlothauer & Wauer Ingenieurgesellschaft für Straßenverkehr mbH",
        company_city="Berlin",
    )

    test_db.rollback_orbis_enrichment_run("run-1")

    with session_scope(test_db.session_factory) as session:
        row = session.query(OrbisEnrichment).filter_by(bvd_id="DE2011435329").first()
        assert row is not None
        assert row.reverted_at is not None
        assert (
            row.company_name == "Schlothauer & Wauer Ingenieurgesellschaft für Straßenverkehr mbH"
        )
        assert row.company_city == "Berlin"
