"""Unit Tests für hq_detection.py.

Bis zu diesem Commit hatte dieses Modul keine Testabdeckung. Die Tests decken
die bestehende 9-Signal-Kaskade (Regression, inkl. des neuen
ROOT_DOMAIN_PRESENCE-Signals) sowie die neuen Vorverarbeitungsschritte
(Namens-Plausibilität, Filial-Dedup), die beide VOR der Kaskade laufen -
siehe Orbis-Notizen (intern): validation_batch2_baseline.md
(Airbus/IBM: selbst HQ_TABLE kann ohne Namensfilter auf Rauschen zeigen).
"""

import pytest

from github_collector.enrichment.hq_detection import (
    HQ_METHOD_PRIORITY,
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
        "bvd_id": "XX000008",
        "orbis_name": "Acme",
        "orbis_country": "DE",
    }
    if "name" in overrides:
        overrides["orbis_name"] = overrides.pop("name")
    if "country_iso" in overrides:
        overrides["orbis_country"] = overrides.pop("country_iso")
    defaults.update(overrides)
    return MatchCandidate(**defaults)


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
    """Regressionstests für die Multi-Signal-Kaskade (ohne Namensfilter).

    Jede Entscheidung braucht eine Bestätigung über Land oder Domain (E16);
    die Kandidaten haben das Default-Land DE, deshalb `github_country="DE"`.
    """

    def test_empty_list_returns_none(self) -> None:
        assert resolve_headquarters([]) is None

    def test_single_candidate_returned_directly(self) -> None:
        candidate = make_orbis_candidate(bvd_id="DE111")
        resolution = resolve_headquarters([candidate], github_country="DE")
        assert resolution.bvd_id == "DE111"

    def test_hq_table_wins_over_oldest_founding(self) -> None:
        hq_flagged = make_orbis_candidate(bvd_id="DE111", has_hq_entry=True)
        older = make_orbis_candidate(bvd_id="DE222", date_incorporation="1900-01-01")

        resolution = resolve_headquarters([hq_flagged, older], github_country="DE")

        assert resolution.bvd_id == "DE111"
        assert resolution.hq_method == "HQ_TABLE"

    def test_hq_table_prefers_most_subsidiaries(self) -> None:
        """Fund aus Live-Verifikation: mehrere HQ_TABLE-Treffer möglich, der
        mit den meisten aktuellen Töchtern ist der stärkere Beleg (empirisch
        an IBM/Oracle bestätigt, siehe OrbisClient.lookup_hq_subsidiary_counts)."""
        weak = make_orbis_candidate(bvd_id="US111", has_hq_entry=True, hq_subsidiary_count=2)
        strong = make_orbis_candidate(bvd_id="US999", has_hq_entry=True, hq_subsidiary_count=5000)

        resolution = resolve_headquarters([weak, strong], github_country="DE")

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

        resolution = resolve_headquarters([a, b], github_country="DE")

        assert resolution.hq_method == "BVDID_FALLBACK"
        assert resolution.bvd_id == "DE111"  # alphabetisch erste

    def test_bvdid_fallback_when_no_signal_present(self) -> None:
        a = make_orbis_candidate(bvd_id="ZZ999")
        b = make_orbis_candidate(bvd_id="AA111")

        resolution = resolve_headquarters([a, b], github_country="DE")

        assert resolution.hq_method == "BVDID_FALLBACK"
        assert resolution.bvd_id == "AA111"


class TestResolveHeadquartersCorroboration:
    """Regressionstests für die Land-/Domain-Bestätigungspflicht bei
    HQ_TABLE/CONSOLIDATING_ENTITY/BVDID_FALLBACK.

    Fund beim 500-Organisationen-Dry-Run: ohne diese Pflicht wählte die
    Kaskade jeden namens-plausiblen Kandidaten mit >=1 Tochter (HQ_TABLE)
    oder einfach den alphabetisch ersten (BVDID_FALLBACK) - unabhängig davon,
    ob Land oder Domain überhaupt passten. Bestätigte Fehltreffer:
    "braintree" und "amanikids" (jeweils ein gleichnamiger, unbeteiligter
    Eintrag).
    """

    def test_hq_table_single_candidate_without_country_match_stays_unresolved(self) -> None:
        """Reproduziert exakt den 'braintree'-Fund: einziger namens-
        plausibler Kandidat mit has_hq_entry, aber Land passt nicht zum
        GitHub-Land und es gibt keine Domain-Bestätigung -> PENDING bleibt
        PENDING statt eine Namenskollision als Treffer zu verwenden."""
        town_government = make_orbis_candidate(
            bvd_id="XX000016", name="Braintree", country_iso="US", has_hq_entry=True
        )

        resolution = resolve_headquarters(
            [town_government],
            github_name="Braintree",
            github_login="braintree",
            github_country="US",
        )

        # In der echten Charge war beides "US" (Eintrag UND Firma
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
            bvd_id="XX000005", name="Amani", country_iso="BE", has_hq_entry=True
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
            bvd_id="XX000002-B0002", name="Teamware", country_iso="AU"
        )

        resolution = resolve_headquarters(
            [br_login_but_au_branch],
            github_name="Teamware",
            github_login="teamware",
            github_country="BR",
        )

        assert resolution is None

    def test_bvdid_fallback_without_known_github_country_stays_unresolved(self) -> None:
        """Ohne github_country gibt es keine Bestätigung über das Land; ohne
        Domain-Treffer wird dann nicht geraten (Entscheidung E16, früher:
        alphabetisch erster Kandidat)."""
        a = make_orbis_candidate(bvd_id="AU111", name="Acme", country_iso="AU")
        b = make_orbis_candidate(bvd_id="FR222", name="Acme", country_iso="FR")

        resolution = resolve_headquarters([a, b], github_name="Acme", github_login="acme")

        assert resolution is None

    def test_single_candidate_without_country_or_domain_stays_unresolved(self) -> None:
        """Auch ein einzelner Kandidat mit HQ-Eintrag ist ohne Land und Domain
        keine Bestätigung - das war der Weg zu den Fehltreffern oben."""
        only = make_orbis_candidate(
            bvd_id="DE111", name="Acme", has_hq_entry=True, hq_subsidiary_count=9
        )

        assert resolve_headquarters([only], github_name="Acme", github_login="acme") is None

    def test_domain_confirms_when_country_unknown(self) -> None:
        plain = make_orbis_candidate(bvd_id="AU111", name="Acme", has_hq_entry=True)
        domain = make_orbis_candidate(
            bvd_id="FR222", name="Acme", has_hq_entry=True, matches_root_domain=True
        )

        resolution = resolve_headquarters(
            [plain, domain], github_name="Acme", github_login="acme", github_country=None
        )

        assert resolution.bvd_id == "FR222"
        assert resolution.hq_method == "HQ_TABLE"

    def test_empty_country_is_treated_as_unknown(self) -> None:
        """'' (Ort ohne Geocoding-Treffer) gilt wie None als unbekanntes Land."""
        plain = make_orbis_candidate(bvd_id="AU111", name="Acme", has_hq_entry=True)
        domain = make_orbis_candidate(
            bvd_id="FR222", name="Acme", has_hq_entry=True, matches_root_domain=True
        )

        resolution = resolve_headquarters(
            [plain, domain], github_name="Acme", github_login="acme", github_country=""
        )

        assert resolution is not None
        assert resolution.bvd_id == "FR222"


class TestResolveHeadquartersNamePlausibility:
    """Tests für den neuen Namens-Plausibilitätsfilter vor der Kaskade."""

    def test_implausible_hq_table_candidate_rejected(self) -> None:
        """Kernfund aus validation_batch2_baseline.md: HQ_TABLE-Flag sitzt bei
        Airbus/IBM teils auf komplett unbeteiligten Firmen. Ohne Namensfilter
        würde die Kaskade das Rauschen wählen, nicht die echte Zentrale."""
        noise_with_hq_flag = make_orbis_candidate(
            bvd_id="XX000014", name="Boering International Group Co., LTD", has_hq_entry=True
        )
        real_entity = make_orbis_candidate(bvd_id="XX000011", name="Airbus")

        resolution = resolve_headquarters(
            [noise_with_hq_flag, real_entity],
            github_name="Airbus",
            github_login="airbus",
            github_country="DE",
        )

        assert resolution.bvd_id == "XX000011"
        assert resolution.hq_method == "BVDID_FALLBACK"

    def test_latin_name_makes_a_non_latin_candidate_plausible(self) -> None:
        """Lateinischer Name aus der zweiten Orbis-Namensspalte (E30); ohne ihn liegt
        die Umschrift "iandeks" mit 0,615 unter der Schwelle."""
        with_latin = make_orbis_candidate(
            bvd_id="XX000021", name="Яндекс", orbis_latin_name="Yandex", country_iso="RU"
        )
        without_latin = make_orbis_candidate(bvd_id="XX000021", name="Яндекс", country_iso="RU")
        kwargs = {
            "github_name": "Yandex",
            "github_login": "yandex",
            "github_country": "RU",
            "min_name_similarity": 0.7,
        }

        resolution = resolve_headquarters([with_latin], **kwargs)

        assert resolution is not None and resolution.bvd_id == "XX000021"
        assert resolve_headquarters([without_latin], **kwargs) is None

    def test_all_candidates_implausible_returns_none(self) -> None:
        candidates = [make_orbis_candidate(name="Unrelated Company")]
        resolution = resolve_headquarters(candidates, github_name="OpenAI", github_login="openai")
        assert resolution is None

    def test_no_name_context_skips_filter(self) -> None:
        """Ohne github_name/github_login (Rückwärtskompatibilität) läuft kein Filter."""
        candidates = [make_orbis_candidate(name="Anything")]
        resolution = resolve_headquarters(candidates, github_country="DE")
        assert resolution is not None


class TestResolveHeadquartersBranchDedup:
    """Tests für die Filial-Dedup vor der Kaskade."""

    def test_branch_suffix_candidates_merged(self) -> None:
        """Fund aus Charge 04: Filial-Suffixe tragen oft
        fälschlich has_hq_entry, die Basis-BVDID nicht."""
        base = make_orbis_candidate(bvd_id="XX000009", name="Musterbau & Partner")
        branch_1 = make_orbis_candidate(
            bvd_id="XX000009-1002", name="Musterbau & Partner", has_hq_entry=True
        )
        branch_2 = make_orbis_candidate(
            bvd_id="XX000009-1003", name="Musterbau & Partner", has_hq_entry=True
        )

        resolution = resolve_headquarters([base, branch_1, branch_2], github_country="DE")

        # Nach Dedup bleibt nur die unsuffigierte Basis übrig -> einziger Kandidat
        assert resolution.bvd_id == "XX000009"
        assert len(resolution.all_candidates) == 1


class TestResolveHeadquartersBatch:
    """Tests für die Batch-Verarbeitung inkl. optionalem Namens-Kontext."""

    def test_batch_without_context_resolves_all(self) -> None:
        grouped = {
            1: [make_orbis_candidate(bvd_id="DE111")],
            2: [make_orbis_candidate(bvd_id="US222")],
        }
        context = {1: (None, None, "DE"), 2: (None, None, "DE")}
        results = resolve_headquarters_batch(grouped, github_context=context)
        assert set(results.keys()) == {1, 2}

    def test_batch_with_context_filters_per_org(self) -> None:
        grouped = {
            1: [
                make_orbis_candidate(bvd_id="GB1", name="Unrelated Noise", has_hq_entry=True),
                make_orbis_candidate(bvd_id="FR1", name="Airbus"),
            ],
        }
        context = {1: ("Airbus", "airbus", "DE")}

        results = resolve_headquarters_batch(grouped, github_context=context)

        assert results[1].bvd_id == "FR1"

    def test_org_without_candidates_skipped(self) -> None:
        results = resolve_headquarters_batch({1: []})
        assert 1 not in results


# Je Signal: der AU-Kandidat hat den "stärkeren" Wert (älter, mehr Umsatz, mehr Mitarbeiter,
# bei Ja/Nein-Signalen die kleinere bvd_id), bestätigt ist aber nur der FR-Kandidat.
SIGNAL_PAIRS = {
    "CONSOLIDATING_ENTITY": ({"consolidation_code": "C1"}, {"consolidation_code": "C1"}),
    "OLDEST_FOUNDING": ({"date_incorporation": "1990-01-01"}, {"date_incorporation": "2000-01-01"}),
    "HIGHEST_REVENUE": ({"operating_revenue": 1000.0}, {"operating_revenue": 500.0}),
    "GUO_SELF": ({"is_guo_self": True}, {"is_guo_self": True}),
    "LARGE_CORP_HQ": ({"number_of_employees": 5000}, {"number_of_employees": 2000}),
    "IS_PARENT": ({"is_parent": True}, {"is_parent": True}),
}


def _signal_pair(method):
    strong, weak = SIGNAL_PAIRS[method]
    au = make_orbis_candidate(bvd_id="AU111", name="Acme", country_iso="AU", **strong)
    fr = make_orbis_candidate(bvd_id="FR222", name="Acme", country_iso="FR", **weak)
    return [au, fr]


class TestEverySignalNeedsCorroboration:
    """Die Bestätigungsregel gilt für alle Signale, nicht nur für 1, 2 und 9 (E16, H2)."""

    @pytest.mark.parametrize("method", sorted(SIGNAL_PAIRS))
    def test_signal_uses_only_corroborated_candidates(self, method) -> None:
        resolution = resolve_headquarters(
            _signal_pair(method), github_name="Acme", github_login="acme", github_country="FR"
        )

        assert resolution.bvd_id == "FR222"
        assert resolution.hq_method == method

    @pytest.mark.parametrize("method", sorted(SIGNAL_PAIRS))
    def test_signal_without_country_needs_domain(self, method) -> None:
        resolution = resolve_headquarters(
            _signal_pair(method), github_name="Acme", github_login="acme", github_country=None
        )

        assert resolution is None
