"""Multi-Signal Hauptsitzerkennung für Orbis Multi-Hit Organizations.

Wenn eine GitHub-Organization mehrere ORBIS-Treffer (BVDIDs) hat, muss genau
ein Treffer als "Hauptsitz" ausgewählt werden. Dieses Modul implementiert die
strikte Prioritätskaskade als wiederverwendbare Python-Logik.

Signale in Prioritätsreihenfolge (siehe HQ_METHOD_PRIORITY unten für die
maßgebliche, tatsächlich ausgeführte Reihenfolge inkl. Coverage-/Fehlerraten;
`config/orbis_matching.yaml`s `headquarters_signals`-Abschnitt ist veraltet
und wird NICHT gelesen):
    1. HQ_TABLE (explizite HQ-Markierung)
    2. CONSOLIDATING_ENTITY (C1/C2-Codes)
    3. ROOT_DOMAIN_PRESENCE (einziger Kandidat mit echter Root-Domain)
    4. OLDEST_FOUNDING (ältestes Gründungsdatum)
    5. HIGHEST_REVENUE (höchster Umsatz)
    6. GUO_SELF (Ultimate Owner Self-Reference)
    7. LARGE_CORP_HQ (>= 1000 Mitarbeiter)
    8. IS_PARENT (Parent-Status in ALL_SUBSIDIARIES_L1)
    99. BVDID_FALLBACK (alphabetisch erster, für Reproduzierbarkeit)

Bekannte Probleme:
    OLDEST_FOUNDING führte in der manuellen Validierung (Stand 2026-03) in
    ~10% der Fälle zu falschen Ländern, weil das älteste Gründungsdatum nicht
    immer dem Hauptsitz entspricht. GUO_SELF hat in der Validierung 0%
    Accuracy für GitHub-Orgs erreicht. Beide Signale bleiben Teil der
    Kaskade, sind aber bewusst unterhalb zuverlässigerer Signale einsortiert
    statt entfernt zu werden -- siehe resolve_headquarters() für Details.

Architektur:
    - Reine Logik, keine DB-Verbindung: Empfängt vorbereitete Dicts.
    - Konfiguration wird aus orbis_matching.yaml gelesen.
    - Kann sowohl von der SQL-Pipeline (als Referenz) als auch von
      Python-Skripten (direkt) verwendet werden.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from github_collector.enrichment.orbis_matching_logic import (
    MatchCandidate,
    calculate_name_similarity,
    select_branch_representative,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Datenstrukturen
# ---------------------------------------------------------------------------
#
# HQ-Auflösung arbeitet auf denselben `MatchCandidate`-Objekten, die die
# Matching-Phase bereits gebaut hat (siehe orbis_matching_logic.py), statt
# auf einer eigenen `OrbisCandidate`-Dataclass. Die beiden Typen trugen bis
# zu diesem Merge dieselben ORBIS-Kernfelder (bvd_id, name/orbis_name,
# country_iso/orbis_country, city, website, total_score) doppelt, plus zwei
# eigene Kopien der Filial-Dedup-Logik (select_branch_representative()) -
# eine reine Übersetzungsschicht (scripts/enrichment/orbis_matching_pipeline.py::
# build_orbis_candidates()/apply_hq_signals()) musste sie bei jedem Lauf
# hin- und herkopieren. Die HQ-spezifischen Felder unten (is_guo_self,
# has_hq_entry, ...) leben jetzt direkt auf MatchCandidate (siehe dortiger
# Kommentar) und werden erst NACH dem Matching von der Pipeline befüllt.


@dataclass
class HQResolution:
    """Ergebnis der Hauptsitz-Auswahl für eine Organization."""

    bvd_id: str
    country_iso: Optional[str]
    hq_method: str
    candidate: MatchCandidate
    all_candidates: List[MatchCandidate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Konstanten: Prioritäts-Reihenfolge der HQ-Methoden
# ---------------------------------------------------------------------------

# Reihenfolge bestimmt die Kaskade. Niedrigere Zahl = höhere Priorität.
# WARNUNG: GUO_SELF hat 0.0% Accuracy in Validierung (März 2026)
# Siehe docs/enrichment/hq_strategy_requirements.md
HQ_METHOD_PRIORITY = {
    "HQ_TABLE": 1,  # Explizite HQ-Markierung (8.3% Coverage, hohe Zuverlässigkeit)
    "CONSOLIDATING_ENTITY": 2,  # C1/C2 Codes (1.2% Coverage)
    "ROOT_DOMAIN_PRESENCE": 3,  # Einziger Kandidat mit echter Root-Domain (siehe unten)
    "OLDEST_FOUNDING": 4,  # Ältestes Gründungsdatum (71.2% Coverage, ~10% Fehlerrate)
    "HIGHEST_REVENUE": 5,  # Höchster Umsatz (34.5% Coverage)
    "GUO_SELF": 6,  # Ultimate Owner Self-Reference (~30% Coverage, 0% Accuracy)
    "LARGE_CORP_HQ": 7,  # Große Unternehmen (>= 1000 Mitarbeiter)
    "IS_PARENT": 8,  # Parent-Status (2.5% Coverage)
    "BVDID_FALLBACK": 99,  # Alphabetischer Fallback (100% Coverage)
}

# ROOT_DOMAIN_PRESENCE (Priorität 3): Fund aus Charge 01 (Microsoft/Google) -
# unter mehreren namensgleichen Kandidaten (Konzernzentrale, Auslandsfilialen,
# interne Investment-Vehikel) hatte in der Praxis nur EINER die tatsächliche
# Root-Domain als WEBSITE hinterlegt; die anderen hatten keine oder eine
# Pfad-/Subdomain-Variante. Muss vom Caller über `matches_root_domain` gesetzt
# werden (Vergleich candidate.website vs. GH-Root-Domain), da dieses Modul
# selbst keine DB-Verbindung hat. Siehe
# workstreams/orbis_matching/docs/batches/batch01_review.md.

# Independence-Indicator Priorität (für GUO_SELF Ranking)
INDEPENDENCE_PRIORITY = {
    "A+": 1,
    "A": 2,
    "A-": 3,
    "U": 4,
}


# ---------------------------------------------------------------------------
# Kernlogik: HQ-Signal-Bestimmung pro Kandidat
# ---------------------------------------------------------------------------


def determine_hq_method(candidate: MatchCandidate) -> str:
    """Bestimme die HQ-Methode für einen einzelnen Kandidaten.

    Gibt den Namen des stärksten verfügbaren Signals zurück.
    Die Priorität entspricht der korrigierten Kaskade basierend auf
    Validierungsergebnissen (März 2026).

    Args:
        candidate: Ein einzelner ORBIS-Treffer mit befüllten Signal-Feldern.

    Returns:
        Name der HQ-Methode (z.B. ``"HQ_TABLE"``, ``"OLDEST_FOUNDING"``).
    """
    if candidate.has_hq_entry:
        return "HQ_TABLE"

    if candidate.consolidation_code in ("C1", "C2"):
        return "CONSOLIDATING_ENTITY"

    if candidate.matches_root_domain:
        return "ROOT_DOMAIN_PRESENCE"

    # Für OLDEST_FOUNDING und HIGHEST_REVENUE brauchen wir den Gruppen-
    # kontext (alle Kandidaten). Deshalb wird das in resolve_headquarters()
    # entschieden. Hier nur die Flags prüfen.
    if candidate.date_incorporation is not None:
        return "OLDEST_FOUNDING"

    if candidate.operating_revenue is not None:
        return "HIGHEST_REVENUE"

    if candidate.is_guo_self:
        return "GUO_SELF"

    if candidate.number_of_employees is not None and candidate.number_of_employees >= 1000:
        return "LARGE_CORP_HQ"

    if candidate.is_parent:
        return "IS_PARENT"

    return "BVDID_FALLBACK"


# ---------------------------------------------------------------------------
# Kernlogik: Multi-Hit Resolution (Hauptsitz-Auswahl)
# ---------------------------------------------------------------------------


# Komplexität bewusst akzeptiert (noqa statt Aufsplitten): die Funktion IST
# die priorisierte 9-Signal-Kaskade (siehe HQ_METHOD_PRIORITY oben) - jedes
# Signal braucht seine eigene Eindeutigkeits-/Corroboration-Prüfung, bevor
# die Kaskade weiterfällt. Ein Aufteilen in Teilfunktionen würde die
# Reihenfolge- und Fallthrough-Logik nur über mehrere Funktionen verteilen,
# ohne sie zu vereinfachen - eher schwerer nachvollziehbar als einfacher.
def resolve_headquarters(  # noqa: C901
    candidates: Sequence[MatchCandidate],
    github_name: Optional[str] = None,
    github_login: Optional[str] = None,
    github_country: Optional[str] = None,
    min_name_similarity: float = 0.5,
) -> Optional[HQResolution]:
    """Wähle genau einen Hauptsitz-Kandidaten aus einer Liste von ORBIS-Treffern.

    Implementiert die korrigierte Prioritätskaskade (basierend auf Validierung März 2026,
    erweitert um ROOT_DOMAIN_PRESENCE nach Charge-01-Fund):
        1. HQ_TABLE (explizite HQ-Markierung, 8.3% Coverage, hohe Zuverlässigkeit)
        2. CONSOLIDATING_ENTITY (C1/C2 Codes, 1.2% Coverage)
        3. ROOT_DOMAIN_PRESENCE (einziger Kandidat mit echter Root-Domain, oder bei
           mehreren via github_country auf einen eindeutigen Landestreffer reduzierbar)
        4. OLDEST_FOUNDING (eindeutig ältestes Gründungsdatum, 71.2% Coverage, ~10% Fehlerrate)
        5. HIGHEST_REVENUE (eindeutig höchster Umsatz, 34.5% Coverage)
        6. GUO_SELF (Ultimate Owner Self-Reference, ~30% Coverage, 0% Accuracy für GitHub-Orgs)
        7. LARGE_CORP_HQ (>= 1000 Mitarbeiter)
        8. IS_PARENT (Parent-Status, 2.5% Coverage)
        9. BVDID_FALLBACK (alphabetisch erste BVDID, bevorzugt innerhalb des
           github_country-Landes falls vorhanden, 100% Coverage)

    Bevor die Kaskade läuft, werden zwei Vorverarbeitungsschritte angewendet
    (Fund aus workstreams/orbis_matching/docs/validation_batch2_baseline.md:
    selbst Signal 1 kann bei Domain-Mehrfachnutzung auf Rauschen zeigen, wenn
    vorher nicht nach Namens-Plausibilität gefiltert wird):
        a) Namens-Plausibilitätsfilter (nur wenn github_name/github_login übergeben)
        b) Filial-Duplikate (gleiche Basis-BVDID) auf einen Repräsentanten reduzieren

    Bei Signal 4 (OLDEST_FOUNDING) und 5 (HIGHEST_REVENUE) wird Eindeutigkeit
    geprüft: Nur wenn genau ein Kandidat das älteste Datum bzw. den höchsten
    Umsatz hat, wird das Signal verwendet. Bei Gleichstand fällt die Kaskade
    weiter.

    Args:
        candidates: Liste von MatchCandidate-Objekten für eine Organization.
        github_name: Anzeigename der GitHub-Org, für Namens-Plausibilitätsfilter.
        github_login: Login der GitHub-Org, für Namens-Plausibilitätsfilter.
        github_country: ISO-Ländercode der GitHub-Org (aus Geocoding), zur
            Disambiguierung von ROOT_DOMAIN_PRESENCE, wenn mehrere Kandidaten
            dieselbe Root-Domain teilen (Airbus/IBM/SAP-Fund: bei echten
            multinationalen Konzernen tragen oft ALLE Landesgesellschaften
            dieselbe Konzern-Website, nicht nur die Zentrale).
        min_name_similarity: Mindest-Ähnlichkeit für den Plausibilitätsfilter.

    Returns:
        HQResolution mit dem ausgewählten Kandidaten, oder None wenn die
        Liste leer ist oder nach dem Namensfilter nichts übrig bleibt.
    """
    if not candidates:
        return None

    candidate_list = list(candidates)

    if github_name or github_login:
        candidate_list = _filter_by_name_plausibility(
            candidate_list, github_name, github_login, min_name_similarity
        )
        if not candidate_list:
            return None

    candidate_list = select_branch_representative(candidate_list)

    # HINWEIS: es gab hier früher einen Shortcut, der einen einzelnen
    # verbleibenden Kandidaten sofort ohne Land-/Domain-Bestätigung
    # akzeptierte. Fund beim 500-Organisationen-Dry-Run: genau dieser Pfad
    # lieferte "braintree" (US-Zahlungsfirma) als einzigen namens-plausiblen
    # Kandidaten die Stadtverwaltung von Braintree, MA - eine reine
    # Namenskollision ohne jede weitere Bestätigung. Ein einzelner Kandidat
    # durchläuft jetzt dieselbe Kaskade (inkl. `_corroborate()`-Prüfung bei
    # HQ_TABLE/CONSOLIDATING_ENTITY/BVDID_FALLBACK) wie mehrere - bei
    # bekanntem, nicht bestätigtem github_country bleibt der Fall PENDING
    # statt geraten zu werden. Funktional identisch zum alten Shortcut,
    # sobald Land entweder unbekannt ist oder übereinstimmt.

    # --- Signal 1: HQ_TABLE ---
    # Bei mehreren HQ-Tabellen-Treffern die mit den meisten aktuellen Töchtern
    # bevorzugen (stärkster Beleg), BVDID nur als letzter Tiebreak.
    #
    # WICHTIG (Fund beim 500-Organisationen-Dry-Run): has_hq_entry allein
    # (mindestens 1 aktuelle Tochter) hat KEINE Mindestschwelle und wird
    # akzeptiert, sobald genau ein namens-plausibler Kandidat es erfüllt -
    # unabhängig davon, ob Land oder Domain überhaupt übereinstimmen. Das
    # führte zu bestätigten Fehltreffern: "braintree" (US-Zahlungsfirma)
    # landete auf der Stadtverwaltung von Braintree, MA (ebenfalls US, aber
    # eine Gemeindebehörde statt der Firma); "amanikids" (Tansania) landete
    # auf einer unabhängigen 2-Personen-IT-Firma "Amani" in Belgien. Deshalb:
    # wenn github_country bekannt ist, zuerst auf länder-passende HQ_TABLE-
    # Kandidaten einschränken. Gibt es keine, aber einen mit echter
    # Root-Domain (matches_root_domain), diesen als Ersatz-Bestätigung
    # akzeptieren. Gibt es WEDER Land- noch Domain-Bestätigung, ist das
    # Signal zu schwach - Kaskade läuft weiter statt eine Rateentscheidung
    # zu erzwingen. Ohne bekanntes github_country (häufig, da GH-Land oft
    # leer) bleibt das bisherige Verhalten (bestmögliche Schätzung ohne
    # Vergleichsbasis) unverändert.
    hq_table = [c for c in candidate_list if c.has_hq_entry]
    if hq_table:
        corroborated = _corroborate(hq_table, github_country)
        if corroborated:
            corroborated.sort(key=lambda c: (-c.hq_subsidiary_count, c.bvd_id))
            return _make_resolution(corroborated[0], "HQ_TABLE", candidate_list)

    # --- Signal 2: CONSOLIDATING_ENTITY ---
    consolidating = [c for c in candidate_list if c.consolidation_code in ("C1", "C2")]
    if consolidating:
        corroborated = _corroborate(consolidating, github_country)
        if corroborated:
            corroborated.sort(key=lambda c: c.bvd_id)
            return _make_resolution(corroborated[0], "CONSOLIDATING_ENTITY", candidate_list)

    # --- Signal 3: ROOT_DOMAIN_PRESENCE (nur wenn eindeutig) ---
    # Fund aus Live-Verifikation (Airbus/IBM/SAP/Salesforce): bei echten
    # multinationalen Konzernen teilen sich oft ALLE Landesgesellschaften
    # dieselbe Konzern-Root-Domain (anders als beim Microsoft/Google-Fund,
    # wo nur die Zentrale eine Website hinterlegt hatte). In diesem Fall ist
    # matches_root_domain für mehrere Kandidaten gleichzeitig True und ein
    # alphabetischer BVDID-Tiebreak landet arbitrar (z.B. AU vor FR). Deshalb:
    # nur eindeutig verwenden, sonst über github_country auf einen
    # Landestreffer einschränken; bleibt es mehrdeutig, Kaskade fortsetzen.
    root_domain_matches = [c for c in candidate_list if c.matches_root_domain]
    if len(root_domain_matches) == 1:
        return _make_resolution(root_domain_matches[0], "ROOT_DOMAIN_PRESENCE", candidate_list)
    if len(root_domain_matches) > 1 and github_country:
        country_matches = [c for c in root_domain_matches if c.orbis_country == github_country]
        if len(country_matches) == 1:
            return _make_resolution(country_matches[0], "ROOT_DOMAIN_PRESENCE", candidate_list)

    # --- Signal 4: OLDEST_FOUNDING (nur wenn eindeutig) ---
    with_founding = [c for c in candidate_list if c.date_incorporation is not None]
    if with_founding:
        oldest_date = min(c.date_incorporation for c in with_founding)
        oldest_candidates = [c for c in with_founding if c.date_incorporation == oldest_date]
        if len(oldest_candidates) == 1:
            return _make_resolution(oldest_candidates[0], "OLDEST_FOUNDING", candidate_list)

    # --- Signal 5: HIGHEST_REVENUE (nur wenn eindeutig) ---
    with_revenue = [c for c in candidate_list if c.operating_revenue is not None]
    if with_revenue:
        max_revenue = max(c.operating_revenue for c in with_revenue)
        top_revenue = [c for c in with_revenue if c.operating_revenue == max_revenue]
        if len(top_revenue) == 1:
            return _make_resolution(top_revenue[0], "HIGHEST_REVENUE", candidate_list)

    # --- Signal 6: GUO_SELF ---
    guo_self = [c for c in candidate_list if c.is_guo_self]
    if guo_self:
        # Sub-Ranking nach Independence Indicator
        guo_self.sort(
            key=lambda c: (
                INDEPENDENCE_PRIORITY.get(c.independence_indicator or "", 99),
                c.bvd_id,
            )
        )
        winner = guo_self[0]
        return _make_resolution(winner, "GUO_SELF", candidate_list)

    # --- Signal 7: LARGE_CORP_HQ ---
    large_corps = [
        c
        for c in candidate_list
        if c.number_of_employees is not None and c.number_of_employees >= 1000
    ]
    if large_corps:
        large_corps.sort(key=lambda c: (-c.number_of_employees, c.bvd_id))
        return _make_resolution(large_corps[0], "LARGE_CORP_HQ", candidate_list)

    # --- Signal 8: IS_PARENT ---
    parents = [c for c in candidate_list if c.is_parent]
    if parents:
        parents.sort(key=lambda c: c.bvd_id)
        return _make_resolution(parents[0], "IS_PARENT", candidate_list)

    # --- Signal 9: BVDID_FALLBACK (länderbewusst, wenn möglich) ---
    # Fund aus Live-Verifikation (Airbus/IBM): sobald ROOT_DOMAIN_PRESENCE bei
    # echten multinationalen Konzernen mangels Eindeutigkeit durchfällt (viele
    # Landesgesellschaften teilen dieselbe Root-Domain UND dasselbe Land -
    # z.B. 16 FR-Airbus-Entitäten unter airbus.com), landete der rein
    # alphabetische Fallback auf einem komplett falschen Land (AU statt FR),
    # weil er ohne jeden Kontext über ALLE Kandidaten sortiert. Bevorzuge
    # daher, falls vorhanden, Kandidaten mit zum GitHub-Land passendem
    # country_iso - das garantiert nicht die exakte HQ-Entität (dafür fehlt
    # der HQ_TABLE-Join, siehe Modul-Docstring der Pipeline), verhindert aber
    # zuverlässig ein falsches Land.
    #
    # WICHTIG (Fund beim 500-Organisationen-Dry-Run, gleicher Fund wie bei
    # Signal 1 oben): ist github_country bekannt, aber KEIN Kandidat bestätigt
    # es (weder Land noch echte Root-Domain), ist "irgendeine" Firma
    # alphabetisch zu wählen reine Raterei - Beispiel "teamware" (GH-Land
    # Brasilien) landete auf einer australischen Niederlassung ohne jeden
    # inhaltlichen Bezug. In diesem Fall lieber unaufgelöst (PENDING) lassen,
    # statt eine unbestätigte Vermutung zu schreiben. Ohne bekanntes
    # github_country (keine Vergleichsbasis) bleibt die bisherige
    # Bestmöglich-Schätzung über alle Kandidaten erhalten.
    fallback_pool = _corroborate(candidate_list, github_country)
    if not fallback_pool:
        return None
    fallback_pool = sorted(fallback_pool, key=lambda c: c.bvd_id)
    return _make_resolution(fallback_pool[0], "BVDID_FALLBACK", candidate_list)


def _corroborate(
    candidates: List[MatchCandidate], github_country: Optional[str]
) -> List[MatchCandidate]:
    """Schränke auf Kandidaten mit Land- oder Domain-Bestätigung ein.

    Siehe Kommentar bei Signal 1 (HQ_TABLE) in `resolve_headquarters()`: ein
    Namens-Treffer allein reicht nicht, um eine Kaskaden-Entscheidung zu
    erzwingen - es braucht mindestens EINE weitere unabhängige Bestätigung
    (Land oder echte Root-Domain).

    Args:
        candidates: Kandidaten, die das jeweilige Signal (z.B. has_hq_entry)
            erfüllen.
        github_country: ISO-Ländercode der GitHub-Org, falls bekannt.

    Returns:
        Länder-passende Kandidaten falls vorhanden, sonst Domain-bestätigte
        Kandidaten falls vorhanden, sonst (nur wenn github_country selbst
        unbekannt ist - keine Vergleichsbasis) alle übergebenen Kandidaten.
        Ist github_country bekannt und weder Land noch Domain bestätigen
        irgendeinen Kandidaten, wird eine leere Liste zurückgegeben (Signal
        gilt als zu schwach).
    """
    if github_country is None:
        return candidates

    country_matches = [c for c in candidates if c.orbis_country == github_country]
    if country_matches:
        return country_matches

    domain_matches = [c for c in candidates if c.matches_root_domain]
    if domain_matches:
        return domain_matches

    return []


def _make_resolution(
    winner: MatchCandidate,
    method: str,
    all_candidates: List[MatchCandidate],
) -> HQResolution:
    """Hilfsfunktion: Erstelle ein HQResolution-Objekt."""
    return HQResolution(
        bvd_id=winner.bvd_id,
        country_iso=winner.orbis_country,
        hq_method=method,
        candidate=winner,
        all_candidates=all_candidates,
    )


def _filter_by_name_plausibility(
    candidates: List[MatchCandidate],
    github_name: Optional[str],
    github_login: Optional[str],
    min_similarity: float,
) -> List[MatchCandidate]:
    """Entferne Kandidaten ohne plausible Namens-Ähnlichkeit zu Name/Login.

    Dünner Wrapper um `calculate_name_similarity()` aus orbis_matching_logic.py.

    Bewusst NICHT durch `filter_implausible_candidates()` aus demselben Modul
    ersetzt, obwohl beide dieselbe Ähnlichkeitsfunktion aufrufen: jene Funktion
    vergleicht gegen die auf JEDEM Kandidaten selbst eingebetteten Felder
    (`c.github_name`/`c.login`, von der Matching-Phase befüllt), während diese
    Funktion `github_name`/`github_login` bewusst als EXTERNEN Parameter für
    die GESAMTE Kandidatenliste nimmt - resolve_headquarters() wird u.a. mit
    Kandidaten aufgerufen, die (noch) keinen Organization-Kontext eingebettet
    haben (siehe Tests in test_hq_detection.py, z.B.
    TestResolveHeadquartersBatch). Beide Varianten liefern in der Pipeline
    dasselbe Ergebnis (dieselbe Org, dieselben Werte), sind aber keine echte
    Duplikation, sondern zwei unterschiedliche, je für ihren Aufrufer passende
    Interfaces auf derselben Kernlogik.

    Args:
        candidates: Kandidaten für eine Organization.
        github_name: Anzeigename der GitHub-Org, falls vorhanden.
        github_login: Login der GitHub-Org, falls vorhanden.
        min_similarity: Mindest-Ähnlichkeit (0.0-1.0).

    Returns:
        Gefilterte Liste; Kandidaten ohne `orbis_name`-Feld werden durchgelassen.
    """
    plausible: List[MatchCandidate] = []
    for c in candidates:
        if not c.orbis_name:
            plausible.append(c)
            continue

        similarity = calculate_name_similarity(c.orbis_name, github_name, github_login or "")
        if similarity >= min_similarity:
            plausible.append(c)
        else:
            logger.debug(
                "HQ-Kandidat verworfen (Namens-Ähnlichkeit %.2f < %.2f): "
                "Orbis '%s' vs. GH Name='%s' Login='%s' (BVDID=%s)",
                similarity,
                min_similarity,
                c.orbis_name,
                github_name,
                github_login,
                c.bvd_id,
            )
    return plausible


# ---------------------------------------------------------------------------
# Batch-Verarbeitung
# ---------------------------------------------------------------------------


def resolve_headquarters_batch(
    grouped_candidates: Dict[Any, Sequence[MatchCandidate]],
    github_context: Optional[Dict[Any, tuple]] = None,
    min_name_similarity: float = 0.5,
) -> Dict[Any, HQResolution]:
    """Wende die HQ-Kaskade auf eine Gruppe von Organizations an.

    Args:
        grouped_candidates: Dict mit Organization-Key (z.B. github_id) als
            Schlüssel und Liste von MatchCandidate als Wert.
        github_context: Optionales Dict mit demselben Organization-Key und
            einem `(github_name, github_login)`- oder
            `(github_name, github_login, github_country)`-Tupel für den
            Namens-Plausibilitätsfilter bzw. die ROOT_DOMAIN_PRESENCE-
            Disambiguierung. Ohne diesen Parameter läuft die Kaskade ohne
            Namensfilter (Rückwärtskompatibilität).
        min_name_similarity: Mindest-Ähnlichkeit für den Plausibilitätsfilter.

    Returns:
        Dict mit Organization-Key und dem jeweiligen HQResolution-Ergebnis.
        Organizations ohne Kandidaten (oder ohne plausible Kandidaten nach
        Namensfilter) werden übersprungen.
    """
    results: Dict[Any, HQResolution] = {}
    github_context = github_context or {}

    for org_key, candidates in grouped_candidates.items():
        context = github_context.get(org_key, (None, None, None))
        github_name, github_login = context[0], context[1]
        github_country = context[2] if len(context) > 2 else None
        resolution = resolve_headquarters(
            candidates,
            github_name=github_name,
            github_login=github_login,
            github_country=github_country,
            min_name_similarity=min_name_similarity,
        )
        if resolution is not None:
            results[org_key] = resolution
        else:
            logger.debug("Keine Kandidaten für Organization %s", org_key)

    unique_resolved = len(results)
    total_input = len(grouped_candidates)
    logger.info(
        "HQ-Resolution abgeschlossen: %d/%d Organizations aufgelöst",
        unique_resolved,
        total_input,
    )

    # Methoden-Verteilung loggen
    method_counts: Dict[str, int] = {}
    for res in results.values():
        method_counts[res.hq_method] = method_counts.get(res.hq_method, 0) + 1

    for method, count in sorted(method_counts.items(), key=lambda x: -x[1]):
        pct = count / unique_resolved * 100 if unique_resolved else 0
        logger.info("  %s: %d (%.1f%%)", method, count, pct)

    return results
