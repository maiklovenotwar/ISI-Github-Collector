#!/usr/bin/env python3
"""Orbis Matching Pipeline: erster echter End-to-End-Lauf.

Verdrahtet zum ersten Mal die bisher ungenutzten Module
`orbis_matching_logic.py` und `hq_detection.py` mit einer echten Datenquelle
(MySQL `organizations`) und einer echten Senke (Hybrid-Schema:
`organizations`-Ergebnisspalten + `orbis_enrichments`-Historie). Die Methodik
ist in docs/methodology/orbis_enrichment.md beschrieben.

WICHTIGE EINSCHRÄNKUNG (bewusst gescoped, siehe Plan "Teil A", GUO/Ownership
als Stretch-Goal markiert): Diese Version holt CONTACT_INFO-Felder (Name,
Land, Stadt, Website) sowie über `OrbisClient.lookup_hq_subsidiary_counts()`
das HQ_TABLE-Signal aus Orbis. CONSOLIDATING_ENTITY, GUO_SELF, HIGHEST_REVENUE,
LARGE_CORP_HQ, IS_PARENT benötigen weitere Joins gegen LEGAL_INFO/
KEY_FINANCIALS_EUR/ALL_SUBSIDIARIES_L1, die hier noch nicht angebunden sind
(GUO_SELF/OLDEST_FOUNDING bewusst NICHT priorisiert - siehe hq_detection.py
Modul-Docstring: historisch die Hauptfehlerquelle bzw. 0% Accuracy für
GH-Orgs). Für PENDING-Fälle greift die Kaskade daher primär auf HQ_TABLE,
ROOT_DOMAIN_PRESENCE und (länderbewussten) BVDID_FALLBACK zurück.

WICHTIGE JOIN-RICHTUNG bei HQ_TABLE (siehe lookup_hq_subsidiary_counts()-
Docstring): Der in orbis_matching_strategy_v2.md dokumentierte Join
`HQ.BVDID = Kandidat.BVDID` liefert für echte Konzernmütter praktisch nie
einen Treffer (in eigenen Tests praktisch nie, deckt sich mit dem
historischen Validierungsbericht). Richtig ist der umgekehrte Join
`HQ.HQ_BVDID = Kandidat.BVDID` ("wie viele Töchter nennen diesen Kandidaten
als Hauptsitz") - empirisch bestätigt an großen Konzernen mit vielen Töchtern.

Usage:
    python scripts/enrichment/orbis_matching_pipeline.py --dry-run --limit 50
    python scripts/enrichment/orbis_matching_pipeline.py --logins airbus,IBM,Netflix --dry-run
    python scripts/enrichment/orbis_matching_pipeline.py --limit 1000
    python scripts/enrichment/orbis_matching_pipeline.py --rollback <run-id>
"""

import argparse
import csv
import logging
import sys
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

# WICHTIG (Fund beim 1000-Organisationen-Dry-Run): ohne basicConfig sind
# sämtliche logger.info()-Aufrufe im gesamten Enrichment-Code (orbis_client.py,
# hq_detection.py, ...) unsichtbar - Python liefert INFO-Meldungen ohne
# konfigurierten Handler nirgendwo aus. Das machte einen 42-minütigen
# Verbindungs-Hänger mitten in der Domain-Abfrage von außen ununterscheidbar
# von normaler Langsamkeit. Ab jetzt sichtbar in stdout/dem Log-File.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from sqlalchemy import create_engine, text  # noqa: E402

from github_collector.config import DB_URL  # noqa: E402
from github_collector.database.database import GitHubDatabase  # noqa: E402
from github_collector.enrichment.hq_detection import (  # noqa: E402
    HQResolution,
    resolve_headquarters,
)
from github_collector.enrichment.orbis_client import (  # noqa: E402
    OrbisClient,
    extract_root_domain,
    is_domain_blacklisted,
    load_blog_blacklist,
)
from github_collector.enrichment.orbis_matching_logic import (  # noqa: E402
    MatchCandidate,
    MatchResult,
    MatchStatus,
    ScoringConfig,
    analyze_matches,
    calculate_name_similarity,
    load_matching_config,
)

# Ab dieser Ähnlichkeit gilt ein Domain-Treffer auch als Namens-Match (für den
# Combo-Bonus) - strenger als der reine Plausibilitäts-Schwellwert (0.5), da
# hier eine echte Übereinstimmung gemeint ist, nicht nur "nicht offensichtlich
# falsch". Ohne diese zweite Stufe würde ein reiner Blog-Match (blog_score=20)
# NIE den min_score von 30 erreichen, siehe Fund beim ersten Live-Testlauf.
NAME_MATCH_SIMILARITY_THRESHOLD = 0.75


def extract_domain(blog_url: Optional[str]) -> Optional[str]:
    """Extrahiere die Domain aus einer Blog-URL (analog zu orbis_combined_export.py)."""
    if not blog_url or not blog_url.strip():
        return None
    url = blog_url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        domain = urllib.parse.urlparse(url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain or None
    except Exception:
        return None


def fetch_organizations(
    limit: Optional[int],
    logins: Optional[List[str]],
) -> List[Dict]:
    """Hole GitHub-Organizations, die noch kein Orbis-Ergebnis haben.

    Args:
        limit: Maximale Anzahl (None = kein Limit).
        logins: Optionale explizite Login-Liste (überschreibt den
            country_code_orbis-IS-NULL-Filter) - für gezielte Validierung
            gegen die von Hand geprüften Chargen-Fälle.

    Returns:
        Liste von Dicts mit id, login, name, blog, country_code.
    """
    engine = create_engine(DB_URL)

    if logins:
        placeholders = ", ".join(f":login{i}" for i in range(len(logins)))
        query = f"""
            SELECT id, login, name, blog, country_code
            FROM organizations
            WHERE login IN ({placeholders})
        """
        params = {f"login{i}": login for i, login in enumerate(logins)}
    else:
        query = """
            SELECT id, login, name, blog, country_code
            FROM organizations
            WHERE country_code_orbis IS NULL
              AND ((name IS NOT NULL AND TRIM(name) != '') OR (login IS NOT NULL AND TRIM(login) != ''))
            ORDER BY id
        """
        params = {}
        if limit:
            query += " LIMIT :limit"
            params["limit"] = limit

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    return [
        {"id": r[0], "login": r[1], "name": r[2] or "", "blog": r[3] or "", "country_code": r[4]}
        for r in rows
    ]


def build_match_candidates(
    org: Dict,
    domain_candidates: List[dict],
    root_domain: Optional[str],
) -> List[MatchCandidate]:
    """Baue MatchCandidate-Objekte aus den rohen Orbis-Domain-Treffern.

    `match_type_input` ist hier immer "name_blog" - unabhängig davon, ob
    `has_name_match` True oder False ist. "login_fallback" (laut
    config/orbis_matching.yaml: "Login-Match, kein Name vorhanden") gehört
    zu einer ganz anderen Situation (siehe build_name_only_candidates())
    und hat mit einem hier ggf. unbestätigten Namen nichts zu tun. Ein
    Domain-Treffer ohne Namensbestätigung (has_name_match=False) erreicht
    ohnehin nur den blog_score (20) - unter min_score (30) - und wird von
    meets_min_score() natürlich herausgefiltert, ohne dass ein eigenes Label
    dafür nötig wäre.
    """
    candidates = []
    for c in domain_candidates:
        orbis_name = c["name"] or ""
        similarity = calculate_name_similarity(orbis_name, org["name"] or None, org["login"])
        has_name_match = similarity >= NAME_MATCH_SIMILARITY_THRESHOLD

        candidates.append(
            MatchCandidate(
                github_id=org["id"],
                login=org["login"],
                match_value=root_domain or "",
                match_type_input="name_blog",
                name_length=len(org["name"]) if org["name"] else len(org["login"]),
                blog_domain=root_domain,
                github_country=org["country_code"],
                github_name=org["name"] or None,
                bvd_id=c["bvd_id"],
                orbis_name=orbis_name,
                orbis_country=c.get("country_iso"),
                city=c.get("city"),
                website=c.get("website"),
                has_name_match=has_name_match,
                has_blog_match=True,
            )
        )
    return candidates


def build_name_only_candidates(
    org: Dict,
    name_candidates: List[dict],
) -> List[MatchCandidate]:
    """Baue MatchCandidate-Objekte aus Namens-Fallback-Treffern (kein Blog).

    Fund aus Charge 10 (studec): frühere Tests des Namens-Fallbacks (Charge
    5-9) prüften ausschließlich sehr obskure Kleinfirmen ohne jede
    Orbis-Abdeckung - dort konnte kein
    Namens-Fallback helfen, weil die Firma schlicht nicht in Orbis erfasst
    ist. studec zeigt den Gegenfall: eine echte, distinktiv benannte Firma
    mit 0 Domain-Treffern (GH-Blog-Domain stimmt nicht mit Orbis' WEBSITE
    überein), aber einem plausiblen Namens-Treffer. Deshalb: Fallback gezielt
    nur bei Domain-Miss UND ausreichend langem/distinktivem Namen (gegen das
    FALS/F3X/Tamas-Rauschproblem aus Charge 5-9), nicht pauschal für alle
    NONE_IN_LIST-artigen Fälle.

    Anders als `build_match_candidates()`: `has_blog_match` ist hier immer
    False (kein Blog-Signal beteiligt). `match_type_input` ist "name_only",
    wenn der Suchbegriff tatsächlich `org["name"]` war - aber "login_fallback"
    (siehe config/orbis_matching.yaml: "Login-Match, kein Name vorhanden"),
    wenn `org["name"]` leer war und der Aufrufer (run_pipeline(), `term =
    org["name"] or org["login"]`) stattdessen mit dem Login gesucht hat.
    Beide Fälle laufen technisch durch dieselbe Namens-Suche, sind aber
    fachlich unterschiedliche Signale mit eigenen Enum-Werten.
    """
    match_type_input = "name_only" if org["name"] else "login_fallback"
    candidates = []
    for c in name_candidates:
        orbis_name = c["name"] or ""
        # Ohne Domain-Bestätigung zählt eine Umschrift nicht als Namens-Treffer (E19);
        # die exakte Namenssuche findet nicht-lateinische Namen ohnehin nicht.
        similarity = calculate_name_similarity(
            orbis_name, org["name"] or None, org["login"], allow_transliteration=False
        )
        has_name_match = similarity >= NAME_MATCH_SIMILARITY_THRESHOLD

        candidates.append(
            MatchCandidate(
                github_id=org["id"],
                login=org["login"],
                match_value=org["name"] or org["login"],
                match_type_input=match_type_input,
                name_length=len(org["name"]) if org["name"] else len(org["login"]),
                blog_domain=None,
                github_country=org["country_code"],
                github_name=org["name"] or None,
                bvd_id=c["bvd_id"],
                orbis_name=orbis_name,
                orbis_country=c.get("country_iso"),
                city=c.get("city"),
                website=c.get("website"),
                has_name_match=has_name_match,
                has_blog_match=False,
            )
        )
    return candidates


def apply_hq_signals(
    match_candidates: List[MatchCandidate],
    root_domain: Optional[str],
    hq_subsidiary_counts: Optional[Dict[str, int]] = None,
) -> List[MatchCandidate]:
    """Befülle die HQ-Auflösungs-Felder direkt auf den vorhandenen
    MatchCandidate-Objekten (mutiert in place), statt sie wie bisher in eine
    separate OrbisCandidate-Kopie zu übersetzen - Matching-Phase und
    HQ-Auflösung arbeiten seit dem Merge der beiden Dataclasses auf denselben
    Kandidaten-Objekten (siehe orbis_matching_logic.py::MatchCandidate).

    Bekannte Lücke (siehe Modul-Docstring): CONSOLIDATING_ENTITY/GUO_SELF/
    HIGHEST_REVENUE/LARGE_CORP_HQ/IS_PARENT-Felder werden hier NICHT befüllt
    (keine zusätzlichen Oracle-Joins in dieser ersten Version). HQ_TABLE wird
    seit der HQ-Subsidiary-Count-Anbindung befüllt (siehe
    OrbisClient.lookup_hq_subsidiary_counts).

    Returns:
        Dieselbe Liste (zur bequemen Verkettung am Call-Site), Objekte sind
        in place mutiert.
    """
    hq_subsidiary_counts = hq_subsidiary_counts or {}
    for mc in match_candidates:
        website_domain = extract_domain(mc.website) if mc.website else None
        mc.matches_root_domain = bool(root_domain) and website_domain == root_domain
        hq_count = hq_subsidiary_counts.get(mc.bvd_id, 0)
        mc.has_hq_entry = hq_count > 0
        mc.hq_subsidiary_count = hq_count
    return match_candidates


def should_attempt_name_fallback(term: Optional[str], threshold: int) -> bool:
    """Gate für den Namens-Fallback: nur ausreichend lange/distinktive Namen.

    Fund aus Charge 10 (studec): der Namens-Fallback hilft bei echten,
    distinktiv benannten Firmen ohne Domain-Treffer. Ohne dieses Gate würde
    er auch bei sehr kurzen/generischen Namen ("FALS", "F3X") greifen und nur
    zufällige, unbeteiligte Treffer liefern (Charge 5-9-Rauschproblem) - siehe
    build_name_only_candidates()-Docstring. Denselben Schwellwert wie der
    Generic-Name-Malus in orbis_matching_logic.py verwenden, damit beide
    Schutzmechanismen konsistent bleiben.
    """
    return bool(term) and len(term) >= threshold


def apply_hq_resolution_to_match_result(
    result: MatchResult, resolution: Optional[HQResolution]
) -> None:
    """Wende ein HQ-Kaskaden-Ergebnis auf ein PENDING MatchResult an (mutiert).

    ZENTRALE INVARIANTE (Fund beim 500-Organisationen-Dry-Run, Ursache der
    bestätigten "braintree"/"amanikids"/"teamware"-Fehltreffer):
    best_candidate MUSS auf den (ggf. fehlenden) Kaskaden-Kandidaten gesetzt
    werden, NIE auf der unbestätigten Rohschätzung aus analyze_matches()
    stehen bleiben. analyze_matches() befüllt best_candidate mit dem
    höchstscorenden Kandidaten, SELBST wenn status=PENDING ist (das ist die
    unbestätigte Rohschätzung, die überhaupt erst zu PENDING statt
    UNIQUE/CONSENSUS führte). Ruft der Aufrufer diese Funktion nicht für JEDEN
    PENDING-Fall auf - auch wenn resolve_headquarters() None liefert - bleibt
    diese unbestätigte Rohschätzung stehen und wird vom Schreibvorgang
    fälschlich als aufgelöst behandelt.

    War bis zu dieser Extraktion als `result.best_candidate = None` vor dem
    Kaskaden-Aufruf plus manuellem Zuweisen danach inline in run_pipeline()
    dupliziert - und genau dieser Reset fehlte im Validierungsskript
    workstreams/orbis_matching/scripts/compare_pipeline_with_reviewed_batches.py,
    das denselben PENDING-Auflösungs-Block unabhängig nachgebaut hatte.

    Args:
        result: Das MatchResult einer PENDING-Organization (wird mutiert).
        resolution: Ergebnis von resolve_headquarters(), oder None wenn die
            Kaskade keinen Kandidaten mit ausreichender Land-/Domain-
            Bestätigung gefunden hat.
    """
    result.best_candidate = resolution.candidate if resolution is not None else None


def run_pipeline(  # noqa: C901
    limit: Optional[int],
    logins: Optional[List[str]],
    dry_run: bool,
    min_name_similarity: Optional[float],
    export_csv: Optional[str] = None,
) -> int:
    """Führe einen kompletten Enrichment-Lauf aus."""
    run_id = str(uuid.uuid4())
    print("=" * 70)
    print("ORBIS MATCHING PIPELINE")
    print("=" * 70)
    print(f"run_id: {run_id}")
    print(f"dry_run: {dry_run}")

    config = load_matching_config()
    scoring_config = ScoringConfig.from_yaml(config)
    if min_name_similarity is not None:
        scoring_config.name_similarity_min_score = min_name_similarity

    blacklist = load_blog_blacklist()

    print("\nLade Organizations...")
    organizations = fetch_organizations(limit=limit, logins=logins)
    print(f"{len(organizations)} Organizations geladen")
    if not organizations:
        print("Keine Organizations zu verarbeiten")
        return 0

    orgs_by_id = {o["id"]: o for o in organizations}

    # Domains extrahieren (inkl. Root-Domain für spätere matches_root_domain-Prüfung)
    domain_for_org: Dict[int, str] = {}
    root_domain_for_org: Dict[int, str] = {}
    for org in organizations:
        domain = extract_domain(org["blog"])
        if not domain or is_domain_blacklisted(domain, blacklist):
            continue
        domain_for_org[org["id"]] = domain
        root_domain_for_org[org["id"]] = extract_root_domain(domain) or domain

    print(f"{len(domain_for_org)} Organizations mit verwertbarer Blog-Domain")

    client = OrbisClient()
    try:
        all_domains = list(set(domain_for_org.values()))
        print(f"\nFrage Orbis für {len(all_domains)} Domains ab (alle Kandidaten)...")
        domain_results = client.lookup_batch_by_websites_all_candidates(
            all_domains, root_domain_blacklist=blacklist
        )

        grouped_candidates: Dict[int, List[MatchCandidate]] = {}
        for org_id, domain in domain_for_org.items():
            raw_candidates = domain_results.get(domain, [])
            grouped_candidates[org_id] = build_match_candidates(
                orgs_by_id[org_id], raw_candidates, root_domain_for_org[org_id]
            )

        # Orgs ohne Domain trotzdem mit leerer Kandidatenliste führen, damit
        # analyze_matches() sie konsistent als NO_MATCH klassifiziert
        for org_id in orgs_by_id:
            grouped_candidates.setdefault(org_id, [])

        # Namens-Fallback: nur für Orgs ohne jeden Domain-Treffer (kein Blog
        # ODER Domain-Lookup lieferte 0 Kandidaten) UND mit ausreichend langem/
        # distinktivem Namen (siehe build_name_only_candidates()-Docstring,
        # Charge 10/studec-Fund). Gate über denselben Schwellwert wie der
        # Generic-Name-Malus, um das FALS/F3X/Tamas-Rauschproblem (Charge 5-9)
        # nicht wieder einzuführen.
        name_fallback_terms: Dict[int, str] = {}
        for org_id, candidates in grouped_candidates.items():
            if candidates:
                continue
            org = orgs_by_id[org_id]
            term = org["name"] or org["login"]
            if should_attempt_name_fallback(term, scoring_config.generic_name_length_threshold):
                name_fallback_terms[org_id] = term

        if name_fallback_terms:
            unique_terms = list(set(name_fallback_terms.values()))
            print(
                f"\nNamens-Fallback für {len(name_fallback_terms)} Organizations ohne "
                f"Domain-Treffer ({len(unique_terms)} eindeutige Namen)..."
            )
            name_results = client.lookup_batch_by_names(unique_terms)
            for org_id, term in name_fallback_terms.items():
                raw_candidates = name_results.get(term.lower(), [])
                if raw_candidates:
                    grouped_candidates[org_id] = build_name_only_candidates(
                        orgs_by_id[org_id], raw_candidates
                    )
            recovered = sum(1 for org_id in name_fallback_terms if grouped_candidates[org_id])
            print(f"{recovered}/{len(name_fallback_terms)} mit mindestens einem Namens-Kandidaten")

        print("\nAnalysiere Matches (Namens-Gate -> Scoring -> Filial-Dedup -> Status)...")
        results = analyze_matches(grouped_candidates, config=scoring_config)

        pending_org_ids = [
            org_id for org_id, result in results.items() if result.status == MatchStatus.PENDING
        ]
        pending_bvd_ids = list(
            {c.bvd_id for org_id in pending_org_ids for c in results[org_id].all_candidates}
        )
        print(
            f"\nFrage HQ-Tochter-Anzahl für {len(pending_bvd_ids)} Kandidaten "
            f"aus {len(pending_org_ids)} PENDING-Fällen ab..."
        )
        hq_subsidiary_counts = (
            client.lookup_hq_subsidiary_counts(pending_bvd_ids) if pending_bvd_ids else {}
        )

        print("\nLöse PENDING-Fälle über die HQ-Kaskade auf...")
        resolved_count = 0
        hq_methods: Dict[int, str] = {}
        for org_id, result in results.items():
            if result.status != MatchStatus.PENDING:
                continue
            org = orgs_by_id[org_id]
            apply_hq_signals(
                result.all_candidates, root_domain_for_org.get(org_id), hq_subsidiary_counts
            )
            resolution = resolve_headquarters(
                result.all_candidates,
                github_name=org["name"] or None,
                github_login=org["login"],
                github_country=org["country_code"],
                min_name_similarity=scoring_config.name_similarity_min_score,
            )
            # Siehe apply_hq_resolution_to_match_result()-Docstring: MUSS für
            # JEDEN PENDING-Fall aufgerufen werden, auch wenn resolution=None
            # ist - sonst bleibt die unbestätigte Rohschätzung aus
            # analyze_matches() stehen (genau der Bug hinter den
            # "braintree"/"amanikids"/"teamware"-Fehltreffern).
            apply_hq_resolution_to_match_result(result, resolution)
            if resolution is None:
                continue
            resolved_count += 1
            hq_methods[org_id] = resolution.hq_method
            print(
                f"  {org['login']}: PENDING -> {resolution.bvd_id} "
                f"(Methode: {resolution.hq_method})"
            )
        print(f"{resolved_count} PENDING-Fälle über die HQ-Kaskade aufgelöst")

        # Ergebnisse zusammenfassen
        status_counts: Dict[str, int] = {}
        for result in results.values():
            status_counts[result.status.value] = status_counts.get(result.status.value, 0) + 1

        print("\nStatus-Verteilung:")
        for status, count in sorted(status_counts.items()):
            print(f"  {status}: {count} ({count / len(results) * 100:.1f}%)")

        if dry_run:
            print("\n[DRY-RUN] Keine Datenbank-Schreibvorgänge durchgeführt.")
            print("Beispiel-Ergebnisse (erste 10 mit Treffer):")
            shown = 0
            for org_id, result in results.items():
                if result.best_candidate is None or shown >= 10:
                    continue
                org = orgs_by_id[org_id]
                print(
                    f"  {org['login']} -> {result.best_candidate.bvd_id} "
                    f"({result.best_candidate.orbis_name}, "
                    f"{result.best_candidate.orbis_country}) "
                    f"[{result.status.value}/{result.match_type.value}]"
                )
                shown += 1

            if export_csv:
                with open(export_csv, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(
                        [
                            "login",
                            "name",
                            "github_country",
                            "status",
                            "match_type",
                            "bvd_id",
                            "company_name",
                            "company_country",
                            "company_city",
                            "company_website",
                            "score",
                            "hq_method",
                            "via_transliteration",
                        ]
                    )
                    for org_id, result in results.items():
                        org = orgs_by_id[org_id]
                        c = result.best_candidate
                        writer.writerow(
                            [
                                org["login"],
                                org["name"],
                                org["country_code"],
                                result.status.value,
                                result.match_type.value if result.match_type else "",
                                c.bvd_id if c else "",
                                c.orbis_name if c else "",
                                c.orbis_country if c else "",
                                c.city if c else "",
                                c.website if c else "",
                                c.total_score if c else "",
                                hq_methods.get(org_id, ""),
                                c.name_via_transliteration if c else "",
                            ]
                        )
                print(f"\n[DRY-RUN] Vollständige Ergebnisse exportiert nach: {export_csv}")
            return 0

        print("\nSchreibe Ergebnisse in die Datenbank...")
        db = GitHubDatabase(DB_URL)
        written = 0
        for org_id, result in results.items():
            if result.best_candidate is None:
                continue
            db.record_orbis_enrichment(
                organization_id=org_id,
                bvd_id=result.best_candidate.bvd_id,
                match_type=result.match_type.value,
                score=result.best_candidate.total_score,
                run_id=run_id,
                country_code=result.best_candidate.orbis_country,
                company_name=result.best_candidate.orbis_name,
                company_city=result.best_candidate.city,
                company_website=result.best_candidate.website,
                hq_method=hq_methods.get(org_id),
                blog_matched=result.best_candidate.has_blog_match,
                enriched_at=datetime.now(timezone.utc),
            )
            written += 1

        print(f"{written} Organizations aktualisiert (run_id={run_id})")
        print(
            f"\nRollback bei Bedarf: python scripts/enrichment/orbis_matching_pipeline.py --rollback {run_id}"
        )
        return 0
    finally:
        client.close()


def run_rollback(run_id: str) -> int:
    """Mache einen Enrichment-Lauf rückgängig."""
    db = GitHubDatabase(DB_URL)
    count = db.rollback_orbis_enrichment_run(run_id)
    print(f"{count} Organizations für run_id={run_id} zurückgesetzt")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Orbis Matching Pipeline")
    parser.add_argument("--limit", type=int, help="Maximale Anzahl Organizations")
    parser.add_argument(
        "--logins",
        type=str,
        help="Komma-getrennte Liste von Logins (für gezielte Validierung gegen "
        "von Hand geprüfte Fälle)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Keine Datenbank-Schreibvorgänge, nur Analyse"
    )
    parser.add_argument(
        "--min-name-similarity",
        type=float,
        default=None,
        help="Überschreibt den Namens-Ähnlichkeits-Schwellwert aus der Config",
    )
    parser.add_argument(
        "--rollback", type=str, metavar="RUN_ID", help="Macht einen Lauf rückgängig"
    )
    parser.add_argument(
        "--export-csv",
        type=str,
        metavar="PATH",
        help="Nur mit --dry-run: schreibt ALLE Ergebnisse (nicht nur die ersten 10) als CSV",
    )

    args = parser.parse_args()

    if args.rollback:
        return run_rollback(args.rollback)

    logins = [login.strip() for login in args.logins.split(",")] if args.logins else None
    return run_pipeline(
        limit=args.limit,
        logins=logins,
        dry_run=args.dry_run,
        min_name_similarity=args.min_name_similarity,
        export_csv=args.export_csv,
    )


if __name__ == "__main__":
    sys.exit(main())
