"""Tests für das dauerhafte Orbis-Enrichment-Logbuch + Match-ID.

Fund beim Testen des echten Schreib-/Rollback-Pfads: rollback_orbis_
enrichment_run() hat OrbisEnrichment-Zeilen bisher komplett GELÖSCHT statt
sie nur als zurückgerollt zu markieren - danach gab es nirgendwo mehr eine
Spur, welche GitHub-Organisation ursprünglich mit welcher Orbis-Firma
verknüpft war. Diese Tests decken die Korrektur ab: Zeilen bleiben für
immer bestehen (`reverted_at` statt DELETE), und `Organization.
current_orbis_enrichment_id` zeigt als "Match-ID" auf die aktuell aktive
Logbuch-Zeile.
"""

from datetime import datetime

from github_collector.database.database import session_scope
from github_collector.database.models import OrbisEnrichment, Organization


def _make_org(test_db, org_id: int, login: str) -> Organization:
    return test_db.insert_organization(
        {"id": org_id, "login": login, "name": login, "created_at": datetime.now()}
    )


def test_record_enrichment_sets_current_enrichment_id(test_db) -> None:
    """Die neu erstellte Logbuch-Zeile wird als "Match-ID" auf der Organisation hinterlegt."""
    org = _make_org(test_db, 1, "BlinkTagInc")

    enrichment = test_db.record_orbis_enrichment(
        organization_id=org.id,
        bvd_id="XX000020",
        match_type="combined_medium",
        score=50,
        run_id="run-1",
        country_code="US",
    )

    with session_scope(test_db.session_factory) as session:
        refreshed = session.query(Organization).filter_by(id=org.id).first()
        assert refreshed.current_orbis_enrichment_id == enrichment.id
        assert refreshed.bvd_id == "XX000020"


def test_rollback_does_not_delete_enrichment_row(test_db) -> None:
    """Kernfund: eine zurückgerollte Zeile bleibt bestehen, nur `reverted_at` wird gesetzt."""
    org = _make_org(test_db, 1, "touchmedia360")
    test_db.record_orbis_enrichment(
        organization_id=org.id,
        bvd_id="XX000015",
        match_type="combined_high",
        score=60,
        run_id="run-1",
        country_code="RO",
    )

    test_db.rollback_orbis_enrichment_run("run-1")

    with session_scope(test_db.session_factory) as session:
        rows = session.query(OrbisEnrichment).filter_by(run_id="run-1").all()
        assert len(rows) == 1, "Zeile darf nicht gelöscht worden sein"
        assert rows[0].reverted_at is not None
        assert rows[0].bvd_id == "XX000015"  # historische Daten bleiben lesbar


def test_rollback_clears_current_enrichment_id_when_nothing_remains(test_db) -> None:
    """Ohne verbleibenden aktiven Match wird current_orbis_enrichment_id (und
    bvd_id/country_code_orbis) auf NULL zurückgesetzt."""
    org = _make_org(test_db, 1, "schlothauer-wauer")
    test_db.record_orbis_enrichment(
        organization_id=org.id,
        bvd_id="XX000009",
        match_type="combined_high",
        score=70,
        run_id="run-1",
        country_code="DE",
    )

    test_db.rollback_orbis_enrichment_run("run-1")

    with session_scope(test_db.session_factory) as session:
        refreshed = session.query(Organization).filter_by(id=org.id).first()
        assert refreshed.bvd_id is None
        assert refreshed.country_code_orbis is None
        assert refreshed.current_orbis_enrichment_id is None


def test_rollback_falls_back_to_earlier_active_enrichment(test_db) -> None:
    """Wird ein zweiter (späterer) Lauf zurückgerollt, springt die
    Organisation wieder auf den noch aktiven früheren Match zurück -
    nicht auf NULL.

    Die beiden Läufe nutzen bewusst unterschiedliche Ländercodes (GB vs.
    FR), nicht denselben - sonst verdeckt ein zufällig identischer Wert
    einen Bug, bei dem country_code_orbis beim Zurückfallen auf den
    früheren Lauf nicht mit aktualisiert wird (siehe
    rollback_orbis_enrichment_run()-Fund)."""
    org = _make_org(test_db, 1, "ajwgroup")
    test_db.record_orbis_enrichment(
        organization_id=org.id,
        bvd_id="XX000013",
        match_type="combined_high",
        score=70,
        run_id="run-1",
        country_code="GB",
        enriched_at=datetime(2026, 1, 1),
    )
    second = test_db.record_orbis_enrichment(
        organization_id=org.id,
        bvd_id="XX000010-0002",
        match_type="combined_high",
        score=70,
        run_id="run-2",
        country_code="FR",
        enriched_at=datetime(2026, 2, 1),
    )

    # run-2 zurückrollen -> run-1 (immer noch aktiv) soll wieder greifen
    test_db.rollback_orbis_enrichment_run("run-2")

    with session_scope(test_db.session_factory) as session:
        refreshed = session.query(Organization).filter_by(id=org.id).first()
        assert refreshed.bvd_id == "XX000013"
        assert refreshed.country_code_orbis == "GB"
        assert refreshed.current_orbis_enrichment_id != second.id

        # run-2s Zeile existiert weiterhin, nur als zurückgerollt markiert
        run2_row = session.query(OrbisEnrichment).filter_by(run_id="run-2").first()
        assert run2_row is not None
        assert run2_row.reverted_at is not None


def test_reverted_enrichment_is_still_queryable_by_bvd_id(test_db) -> None:
    """Genau der Anwendungsfall aus der Nutzerfrage: auch nach einem Rollback
    lässt sich über orbis_enrichments nachvollziehen, welche GitHub-Org
    ursprünglich zu einer BVDID gehörte."""
    org = _make_org(test_db, 1, "otoci")
    test_db.record_orbis_enrichment(
        organization_id=org.id,
        bvd_id="YYVS8133889",
        match_type="combined_high",
        score=50,
        run_id="run-1",
    )
    test_db.rollback_orbis_enrichment_run("run-1")

    with session_scope(test_db.session_factory) as session:
        history = session.query(OrbisEnrichment).filter_by(bvd_id="YYVS8133889").all()
        assert len(history) == 1
        assert history[0].organization_id == org.id
        assert history[0].reverted_at is not None
