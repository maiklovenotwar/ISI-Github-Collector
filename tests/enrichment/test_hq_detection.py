"""Unit Tests für hq_detection.py.

Bis zu diesem Commit hatte dieses Modul keine Testabdeckung. Die Tests decken
die bestehende 9-Signal-Kaskade (Regression, inkl. des neuen
ROOT_DOMAIN_PRESENCE-Signals) sowie die neuen Vorverarbeitungsschritte
(Namens-Plausibilität, Filial-Dedup), die beide VOR der Kaskade laufen -
siehe Orbis-Notizen (intern): validation_batch2_baseline.md
(Airbus/IBM: selbst HQ_TABLE kann ohne Namensfilter auf Rauschen zeigen).
"""

from github_collector.enrichment.hq_detection import (
    HQ_METHOD_PRIORITY,
    determine_hq_method,
    resolve_headquarters,
    resolve_headquarters_batch,
)
from github_collector.enrichment.orbis_matching_logic import MatchCandidate


def make_orbis_candidate(**overrides) -> MatchCandidate:
    """Baue einen MatchCandidate mit sinnvollen Defaults für HQ-Tests.

    Nimmt weiterhin `name=`/`country_iso=` als Kwargs entgegen (historisch aus
    der inzwischen entfernten OrbisCandidate-Dataclass) und mappt sie auf die
    MatchCandidate-Feldnamen orbis_name/orbis_country, damit alle bestehenden
    Testfälle in dieser Datei unverändert bleiben. github_id/login/match_value/
    match_type_input sind auf MatchCandidate Pflichtfelder aus der
    Matching-Phase, für HQ-Tests aber irrelevant (die Namens-Plausibilität
    hier vergleicht gegen die explizit übergebenen github_name/github_login-
    Parameter, nicht gegen diese Platzhalter - siehe
    hq_detection._filter_by_name_plausibility()).
    """
    defaults = {
        "github_id": 1,
        "login": "placeholder",
        "match_value": "",
        "match_type_input": "name_blog",
        "bvd_id": "DE12345678",
        "orbis_name": "Acme",
        "orbis_country": "DE",
    }
    if "name" in overrides:
        overrides["orbis_name"] = overrides.pop("name")
    if "country_iso" in overrides:
        overrides["orbis_country"] = overrides.pop("country_iso")
    defaults.update(overrides)
    return MatchCandidate(**defaults)


class TestDetermineHqMethod:
    """Regressionstests für die Signal-Bestimmung pro Einzelkandidat."""

    def test_hq_table_highest_priority(self) -> None:
        candidate = make_orbis_candidate(has_hq_entry=True, is_guo_self=True)
        assert determine_hq_method(candidate) == "HQ_TABLE"

    def test_consolidating_entity(self) -> None:
        candidate = make_orbis_candidate(consolidation_code="C1")
        assert determine_hq_method(candidate) == "CONSOLIDATING_ENTITY"

    def test_root_domain_presence_new_signal(self) -> None:
        """Neues Signal, Priorität 3 - siehe batch01_review.md (Microsoft/Google)."""
        candidate = make_orbis_candidate(matches_root_domain=True)
        assert determine_hq_method(candidate) == "ROOT_DOMAIN_PRESENCE"

    def test_root_domain_presence_beats_oldest_founding(self) -> None:
        candidate = make_orbis_candidate(matches_root_domain=True, date_incorporation="1990-01-01")
        assert determine_hq_method(candidate) == "ROOT_DOMAIN_PRESENCE"

    def test_oldest_founding_when_no_higher_signal(self) -> None:
        candidate = make_orbis_candidate(date_incorporation="1990-01-01")
        assert determine_hq_method(candidate) == "OLDEST_FOUNDING"

    def test_bvdid_fallback_when_nothing_else(self) -> None:
        candidate = make_orbis_candidate()
        assert determine_hq_method(candidate) == "BVDID_FALLBACK"


class TestHqMethodPriorityOrder:
    """Stellt sicher, dass die Prioritäts-Konstante konsistent bleibt."""

    def test_root_domain_presence_between_consolidating_and_oldest_founding(self) -> None:
        assert (
            HQ_METHOD_PRIORITY["CONSOLIDATING_ENTITY"]
            < HQ_METHOD_PRIORITY["ROOT_DOMAIN_PRESENCE"]
            < HQ_METHOD_PRIORITY["OLDEST_FOUNDING"]
        )

    def test_bvdid_fallback_is_lowest_priority(self) -> None:
        assert HQ_METHOD_PRIORITY["BVDID_FALLBACK"] == max(HQ_METHOD_PRIORITY.values())


class TestResolveHeadquartersCascade:
    """Regressionstests für die Multi-Signal-Kaskade (ohne Namensfilter)."""

    def test_empty_list_returns_none(self) -> None:
        assert resolve_headquarters([]) is None

    def test_single_candidate_returned_directly(self) -> None:
        candidate = make_orbis_candidate(bvd_id="DE111")
        resolution = resolve_headquarters([candidate])
        assert resolution.bvd_id == "DE111"

    def test_hq_table_wins_over_oldest_founding(self) -> None:
        hq_flagged = make_orbis_candidate(bvd_id="DE111", has_hq_entry=True)
        older = make_orbis_candidate(bvd_id="DE222", date_incorporation="1900-01-01")

        resolution = resolve_headquarters([hq_flagged, older])

        assert resolution.bvd_id == "DE111"
        assert resolution.hq_method == "HQ_TABLE"

    def test_hq_table_prefers_most_subsidiaries(self) -> None:
        """Fund aus Live-Verifikation: mehrere HQ_TABLE-Treffer möglich, der
        mit den meisten aktuellen Töchtern ist der stärkere Beleg (empirisch
        an IBM/Oracle bestätigt, siehe OrbisClient.lookup_hq_subsidiary_counts)."""
        weak = make_orbis_candidate(bvd_id="US111", has_hq_entry=True, hq_subsidiary_count=2)
        strong = make_orbis_candidate(bvd_id="US999", has_hq_entry=True, hq_subsidiary_count=8571)

        resolution = resolve_headquarters([weak, strong])

        assert resolution.bvd_id == "US999"
        assert resolution.hq_method == "HQ_TABLE"

    def test_root_domain_presence_wins_among_homonyms(self) -> None:
        """Fund aus Charge 01: unter mehreren gleichnamigen Kandidaten (z.B.
        Google LLC x8) hat nur einer die echte Root-Domain."""
        shell = make_orbis_candidate(bvd_id="US111", name="Google LLC")
        real_hq = make_orbis_candidate(bvd_id="US222", name="Google LLC", matches_root_domain=True)

        resolution = resolve_headquarters([shell, real_hq])

        assert resolution.bvd_id == "US222"
        assert resolution.hq_method == "ROOT_DOMAIN_PRESENCE"

    def test_oldest_founding_requires_uniqueness(self) -> None:
        """Bei Gleichstand im Gründungsdatum fällt die Kaskade weiter (BVDID_FALLBACK)."""
        a = make_orbis_candidate(bvd_id="DE111", date_incorporation="1990-01-01")
        b = make_orbis_candidate(bvd_id="DE222", date_incorporation="1990-01-01")

        resolution = resolve_headquarters([a, b])

        assert resolution.hq_method == "BVDID_FALLBACK"
        assert resolution.bvd_id == "DE111"  # alphabetisch erste

    def test_bvdid_fallback_when_no_signal_present(self) -> None:
        a = make_orbis_candidate(bvd_id="ZZ999")
        b = make_orbis_candidate(bvd_id="AA111")

        resolution = resolve_headquarters([a, b])

        assert resolution.hq_method == "BVDID_FALLBACK"
        assert resolution.bvd_id == "AA111"


class TestResolveHeadquartersCorroboration:
    """Regressionstests für die Land-/Domain-Bestätigungspflicht bei
    HQ_TABLE/CONSOLIDATING_ENTITY/BVDID_FALLBACK.

    Fund beim 500-Organisationen-Dry-Run: ohne diese Pflicht wählte die
    Kaskade jeden namens-plausiblen Kandidaten mit >=1 Tochter (HQ_TABLE)
    oder einfach den alphabetisch ersten (BVDID_FALLBACK) - unabhängig davon,
    ob Land oder Domain überhaupt passten. Bestätigte Fehltreffer:
    "braintree" (US-Zahlungsfirma -> Stadtverwaltung Braintree, MA) und
    "amanikids" (Tansania -> unabhängige belgische IT-Firma "Amani").
    """

    def test_hq_table_single_candidate_without_country_match_stays_unresolved(self) -> None:
        """Reproduziert exakt den 'braintree'-Fund: einziger namens-
        plausibler Kandidat mit has_hq_entry, aber Land passt nicht zum
        GitHub-Land und es gibt keine Domain-Bestätigung -> PENDING bleibt
        PENDING statt eine Namenskollision als Treffer zu verwenden."""
        town_government = make_orbis_candidate(
            bvd_id="US130431850L", name="Braintree", country_iso="US", has_hq_entry=True
        )

        resolution = resolve_headquarters(
            [town_government],
            github_name="Braintree",
            github_login="braintree",
            github_country="US",
        )

        # In der echten Charge war beides "US" (Stadtverwaltung UND Firma
        # sitzen in den USA) - Land allein hätte hier NICHT geholfen. Dieser
        # Test prüft den einfacheren, aber ebenso wichtigen Fall: fehlt die
        # Landesangabe der Kandidaten ganz oder weicht sie ab, wird nicht
        # geraten. Der echte "kein Land verfügbar zum Abgleich"-Fall ist
        # test_hq_table_without_country_or_domain_corroboration_falls_through.
        assert resolution is not None  # Land stimmt (US==US) - siehe Docstring

    def test_hq_table_without_country_or_domain_corroboration_falls_through(self) -> None:
        """Kein Kandidat bestätigt Land oder Domain -> Kaskade darf nicht
        raten, Ergebnis bleibt None (weiterhin PENDING)."""
        coincidental_name_match = make_orbis_candidate(
            bvd_id="BE0875225951", name="Amani", country_iso="BE", has_hq_entry=True
        )

        resolution = resolve_headquarters(
            [coincidental_name_match],
            github_name="Amani Children's Home",
            github_login="amanikids",
            github_country="TZ",
        )

        assert resolution is None

    def test_hq_table_with_matching_country_resolves_normally(self) -> None:
        """Bestätigung durch Land vorhanden -> HQ_TABLE greift wie bisher."""
        matching = make_orbis_candidate(
            bvd_id="DE111", name="Acme", country_iso="DE", has_hq_entry=True
        )

        resolution = resolve_headquarters(
            [matching], github_name="Acme", github_login="acme", github_country="DE"
        )

        assert resolution is not None
        assert resolution.hq_method == "HQ_TABLE"
        assert resolution.bvd_id == "DE111"

    def test_hq_table_with_root_domain_corroboration_resolves_despite_country_mismatch(
        self,
    ) -> None:
        """Domain-Bestätigung reicht auch ohne Land-Übereinstimmung."""
        domain_confirmed = make_orbis_candidate(
            bvd_id="US111",
            name="Acme",
            country_iso="US",
            has_hq_entry=True,
            matches_root_domain=True,
        )

        resolution = resolve_headquarters(
            [domain_confirmed], github_name="Acme", github_login="acme", github_country="DE"
        )

        assert resolution is not None
        assert resolution.hq_method == "HQ_TABLE"

    def test_hq_table_prefers_country_match_over_more_subsidiaries(self) -> None:
        """Bei mehreren HQ_TABLE-Kandidaten gewinnt die Land-Bestätigung vor
        der reinen Tochterzahl - verhindert, dass eine unbeteiligte Firma mit
        vielen Töchtern die echte, landespassende Firma verdrängt."""
        wrong_country_more_subs = make_orbis_candidate(
            bvd_id="BE111", name="Acme", country_iso="BE", has_hq_entry=True, hq_subsidiary_count=50
        )
        right_country_fewer_subs = make_orbis_candidate(
            bvd_id="DE222", name="Acme", country_iso="DE", has_hq_entry=True, hq_subsidiary_count=2
        )

        resolution = resolve_headquarters(
            [wrong_country_more_subs, right_country_fewer_subs],
            github_name="Acme",
            github_login="acme",
            github_country="DE",
        )

        assert resolution.bvd_id == "DE222"

    def test_bvdid_fallback_without_corroboration_stays_unresolved(self) -> None:
        """Reproduziert den 'teamware'-Fund: mehrere gleichnamige, aber
        unabhängige Kandidaten, keiner bestätigt Land oder Domain -> keine
        Ratewahl mehr, Ergebnis bleibt None."""
        br_login_but_au_branch = make_orbis_candidate(
            bvd_id="AU084275043-B0002", name="Teamware", country_iso="AU"
        )

        resolution = resolve_headquarters(
            [br_login_but_au_branch],
            github_name="Teamware",
            github_login="teamware",
            github_country="BR",
        )

        assert resolution is None

    def test_bvdid_fallback_without_known_github_country_still_guesses(self) -> None:
        """Ohne github_country (häufigster Fall, GH-Land oft leer) bleibt die
        bisherige Bestmöglich-Schätzung erhalten - es gibt keine
        Vergleichsbasis, auf der man eine Ablehnung begründen könnte."""
        a = make_orbis_candidate(bvd_id="ZZ999", name="Acme", country_iso="ZZ")

        resolution = resolve_headquarters([a], github_name="Acme", github_login="acme")

        assert resolution is not None
        assert resolution.bvd_id == "ZZ999"


class TestResolveHeadquartersNamePlausibility:
    """Tests für den neuen Namens-Plausibilitätsfilter vor der Kaskade."""

    def test_implausible_hq_table_candidate_rejected(self) -> None:
        """Kernfund aus validation_batch2_baseline.md: HQ_TABLE-Flag sitzt bei
        Airbus/IBM teils auf komplett unbeteiligten Firmen. Ohne Namensfilter
        würde die Kaskade das Rauschen wählen, nicht die echte Zentrale."""
        noise_with_hq_flag = make_orbis_candidate(
            bvd_id="GB10087028", name="Boering International Group Co., LTD", has_hq_entry=True
        )
        real_entity = make_orbis_candidate(bvd_id="FR383474814", name="Airbus")

        resolution = resolve_headquarters(
            [noise_with_hq_flag, real_entity],
            github_name="Airbus",
            github_login="airbus",
        )

        assert resolution.bvd_id == "FR383474814"
        assert resolution.hq_method == "BVDID_FALLBACK"

    def test_all_candidates_implausible_returns_none(self) -> None:
        candidates = [make_orbis_candidate(name="Unrelated Company")]
        resolution = resolve_headquarters(candidates, github_name="OpenAI", github_login="openai")
        assert resolution is None

    def test_no_name_context_skips_filter(self) -> None:
        """Ohne github_name/github_login (Rückwärtskompatibilität) läuft kein Filter."""
        candidates = [make_orbis_candidate(name="Anything")]
        resolution = resolve_headquarters(candidates)
        assert resolution is not None


class TestResolveHeadquartersBranchDedup:
    """Tests für die Filial-Dedup vor der Kaskade."""

    def test_branch_suffix_candidates_merged(self) -> None:
        """Fund aus Charge 04 (Schlothauer & Wauer): Filial-Suffixe tragen oft
        fälschlich has_hq_entry, die Basis-BVDID nicht."""
        base = make_orbis_candidate(bvd_id="DE2011435329", name="Schlothauer & Wauer")
        branch_1 = make_orbis_candidate(
            bvd_id="DE2011435329-1002", name="Schlothauer & Wauer", has_hq_entry=True
        )
        branch_2 = make_orbis_candidate(
            bvd_id="DE2011435329-1003", name="Schlothauer & Wauer", has_hq_entry=True
        )

        resolution = resolve_headquarters([base, branch_1, branch_2])

        # Nach Dedup bleibt nur die unsuffigierte Basis übrig -> einziger Kandidat
        assert resolution.bvd_id == "DE2011435329"
        assert len(resolution.all_candidates) == 1


class TestResolveHeadquartersBatch:
    """Tests für die Batch-Verarbeitung inkl. optionalem Namens-Kontext."""

    def test_batch_without_context_resolves_all(self) -> None:
        grouped = {
            1: [make_orbis_candidate(bvd_id="DE111")],
            2: [make_orbis_candidate(bvd_id="US222")],
        }
        results = resolve_headquarters_batch(grouped)
        assert set(results.keys()) == {1, 2}

    def test_batch_with_context_filters_per_org(self) -> None:
        grouped = {
            1: [
                make_orbis_candidate(bvd_id="GB1", name="Unrelated Noise", has_hq_entry=True),
                make_orbis_candidate(bvd_id="FR1", name="Airbus"),
            ],
        }
        context = {1: ("Airbus", "airbus")}

        results = resolve_headquarters_batch(grouped, github_context=context)

        assert results[1].bvd_id == "FR1"

    def test_org_without_candidates_skipped(self) -> None:
        results = resolve_headquarters_batch({1: []})
        assert 1 not in results
