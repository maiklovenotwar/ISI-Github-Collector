"""Unit Tests für scripts/enrichment/orbis_matching_pipeline.py.

Bis zu diesem Commit hatte dieses Skript keine Testabdeckung (siehe
Architecture-Review-Kandidat B: die Zusammenführung von MatchCandidate/
OrbisCandidate ändert genau die Funktion, die hier getestet wird -
apply_hq_signals(), vormals build_orbis_candidates()). Deckt nur die reinen,
DB-unabhängigen Funktionen ab, nicht run_pipeline() selbst (DB/Oracle-Zugriff).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts" / "enrichment"))

from orbis_matching_pipeline import (  # noqa: E402
    apply_hq_resolution_to_match_result,
    apply_hq_signals,
    should_attempt_name_fallback,
)

from github_collector.enrichment.hq_detection import (  # noqa: E402
    HQResolution,
    resolve_headquarters,
)
from github_collector.enrichment.orbis_matching_logic import (  # noqa: E402
    MatchCandidate,
    MatchResult,
    MatchStatus,
)


def make_candidate(**overrides) -> MatchCandidate:
    """Baue einen MatchCandidate mit sinnvollen Defaults für Pipeline-Tests."""
    defaults = {
        "github_id": 1,
        "login": "acme",
        "match_value": "acme.com",
        "match_type_input": "name_blog",
        "bvd_id": "XX000008",
        "orbis_name": "Acme",
        "orbis_country": "DE",
        "website": "acme.com",
    }
    defaults.update(overrides)
    return MatchCandidate(**defaults)


class TestApplyHqSignalsRootDomain:
    """Tests für das matches_root_domain-Signal."""

    def test_matching_website_domain_sets_flag(self) -> None:
        candidate = make_candidate(website="acme.com")
        apply_hq_signals([candidate], root_domain="acme.com")
        assert candidate.matches_root_domain is True

    def test_different_website_domain_does_not_set_flag(self) -> None:
        candidate = make_candidate(website="other.com")
        apply_hq_signals([candidate], root_domain="acme.com")
        assert candidate.matches_root_domain is False

    def test_no_root_domain_does_not_set_flag(self) -> None:
        candidate = make_candidate(website="acme.com")
        apply_hq_signals([candidate], root_domain=None)
        assert candidate.matches_root_domain is False

    def test_no_website_does_not_set_flag(self) -> None:
        candidate = make_candidate(website=None)
        apply_hq_signals([candidate], root_domain="acme.com")
        assert candidate.matches_root_domain is False


class TestApplyHqSignalsHqTable:
    """Tests für has_hq_entry/hq_subsidiary_count aus lookup_hq_subsidiary_counts()."""

    def test_positive_count_sets_has_hq_entry(self) -> None:
        candidate = make_candidate(bvd_id="XX000017")
        apply_hq_signals([candidate], root_domain=None, hq_subsidiary_counts={"XX000017": 8571})
        assert candidate.has_hq_entry is True
        assert candidate.hq_subsidiary_count == 8571

    def test_missing_bvd_id_defaults_to_no_hq_entry(self) -> None:
        candidate = make_candidate(bvd_id="US999")
        apply_hq_signals([candidate], root_domain=None, hq_subsidiary_counts={"XX000017": 8571})
        assert candidate.has_hq_entry is False
        assert candidate.hq_subsidiary_count == 0

    def test_none_hq_subsidiary_counts_defaults_to_no_hq_entry(self) -> None:
        candidate = make_candidate(bvd_id="XX000017")
        apply_hq_signals([candidate], root_domain=None, hq_subsidiary_counts=None)
        assert candidate.has_hq_entry is False


class TestApplyHqSignalsMutation:
    """Tests für die In-Place-Mutation (kein Kopieren mehr in eine separate Dataclass)."""

    def test_mutates_in_place_and_returns_same_list(self) -> None:
        candidate = make_candidate()
        original_list = [candidate]

        result = apply_hq_signals(original_list, root_domain=None)

        assert result is original_list
        assert result[0] is candidate

    def test_empty_list_is_noop(self) -> None:
        assert apply_hq_signals([], root_domain="acme.com") == []


class TestApplyHqSignalsThenResolveHeadquarters:
    """End-to-End: derselbe MatchCandidate fließt von der Matching-Phase direkt
    in resolve_headquarters() - der eigentliche Zweck des Merges von
    MatchCandidate/OrbisCandidate. Vorher lief hier eine Übersetzung in eine
    separate OrbisCandidate-Kopie; dieser Test stellt sicher, dass das
    HQResolution.candidate-Objekt jetzt tatsächlich dasselbe Objekt ist, das
    schon die Matching-Phase gebaut hat (keine Kopie mehr)."""

    def test_resolution_candidate_is_the_same_object_matching_produced(self) -> None:
        candidate = make_candidate(
            bvd_id="XX000017", orbis_name="Acme", website="acme.com", github_name="Acme"
        )
        apply_hq_signals(
            [candidate],
            root_domain="acme.com",
            hq_subsidiary_counts={"XX000017": 100},
        )

        resolution = resolve_headquarters(
            [candidate], github_name="Acme", github_login="acme", github_country="DE"
        )

        assert resolution is not None
        assert resolution.candidate is candidate
        assert resolution.hq_method == "HQ_TABLE"


class TestShouldAttemptNameFallback:
    """Tests für das Namens-Fallback-Gate (Charge-10-studec-/Charge-5-9-Fund)."""

    def test_long_enough_term_passes(self) -> None:
        assert should_attempt_name_fallback("Studec", threshold=5) is True

    def test_term_below_threshold_rejected(self) -> None:
        assert should_attempt_name_fallback("FALS", threshold=5) is False

    def test_term_exactly_at_threshold_passes(self) -> None:
        assert should_attempt_name_fallback("Acme5", threshold=5) is True

    def test_none_term_rejected(self) -> None:
        assert should_attempt_name_fallback(None, threshold=5) is False

    def test_empty_term_rejected(self) -> None:
        assert should_attempt_name_fallback("", threshold=5) is False


def make_match_result(**overrides) -> MatchResult:
    """Baue ein MatchResult mit sinnvollen Defaults für Pipeline-Tests."""
    defaults = {
        "github_id": 1,
        "login": "acme",
        "status": MatchStatus.PENDING,
    }
    defaults.update(overrides)
    return MatchResult(**defaults)


class TestApplyHqResolutionToMatchResult:
    """Regressionstests für die zentrale Reset-vor-Write-Invariante - Ursache
    der bestätigten 'braintree'/'amanikids'/'teamware'-Fehltreffer, siehe
    Docstring von apply_hq_resolution_to_match_result()."""

    def test_none_resolution_clears_unconfirmed_guess(self) -> None:
        """Kernregression: analyze_matches() hat bereits eine unbestätigte
        Rohschätzung in best_candidate gesetzt (das ist es, was PENDING
        überhaupt erst ausgelöst hat) - ohne den Reset würde diese Schätzung
        unverändert als 'aufgelöst' weitergereicht."""
        unconfirmed_guess = make_candidate(bvd_id="XX000016", orbis_name="Braintree")
        result = make_match_result(best_candidate=unconfirmed_guess)

        apply_hq_resolution_to_match_result(result, resolution=None)

        assert result.best_candidate is None

    def test_resolution_sets_its_candidate(self) -> None:
        winner = make_candidate(bvd_id="US222")
        resolution = HQResolution(
            bvd_id="US222", country_iso="US", hq_method="HQ_TABLE", candidate=winner
        )
        result = make_match_result(best_candidate=None)

        apply_hq_resolution_to_match_result(result, resolution)

        assert result.best_candidate is winner

    def test_resolution_replaces_previous_unconfirmed_guess(self) -> None:
        """Die Kaskade kann einen ANDEREN Kandidaten wählen als den
        höchstscorenden aus analyze_matches() - der alte Wert darf nicht
        einfach stehen bleiben oder additiv kombiniert werden."""
        raw_guess = make_candidate(bvd_id="US111")
        winner = make_candidate(bvd_id="US222")
        resolution = HQResolution(
            bvd_id="US222", country_iso="US", hq_method="HQ_TABLE", candidate=winner
        )
        result = make_match_result(best_candidate=raw_guess)

        apply_hq_resolution_to_match_result(result, resolution)

        assert result.best_candidate is winner
        assert result.best_candidate is not raw_guess
