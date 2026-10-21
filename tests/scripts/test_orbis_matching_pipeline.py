"""Unit Tests für scripts/enrichment/orbis_matching_pipeline.py.

Bis zu diesem Commit hatte dieses Skript keine Testabdeckung (siehe
Architecture-Review-Kandidat B: die Zusammenführung von MatchCandidate/
OrbisCandidate ändert genau die Funktion, die hier getestet wird -
apply_hq_signals(), vormals build_orbis_candidates()). Deckt nur die reinen,
DB-unabhängigen Funktionen ab, nicht run_pipeline() selbst (DB/Oracle-Zugriff).
"""

import csv
import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO / "scripts" / "enrichment"))

from orbis_matching_pipeline import (  # noqa: E402
    OUTCOME_COLUMNS,
    apply_hq_resolution_to_match_result,
    apply_hq_signals,
    build_match_candidates,
    build_name_only_candidates,
    build_run_metadata,
    export_paths,
    outcome_rows,
    search_path_for,
    should_attempt_name_fallback,
    write_outcome_export,
)


class TestOutcomeExport:
    """Vollständige Ergebnisliste je Lauf mit Laufmetadaten (task 6, E26)."""

    def test_both_export_files_must_be_ignored(self) -> None:
        # test_export_*.csv is ignored, test_export_*.meta.json is not.
        with pytest.raises(ValueError, match="not ignored"):
            export_paths("test_export_run.csv")
        target = REPO / "workstreams" / "orbis_matching" / "data" / "run.csv"
        assert export_paths(str(target)) == (target, target.with_name("run.meta.json"))

    def test_search_path_names_how_an_organization_was_searched(self) -> None:
        domains = {1: "acme.com"}
        names = {2: "Acme Widgets"}
        assert search_path_for(1, domains, names) == "domain"
        assert search_path_for(2, domains, names) == "name"
        assert search_path_for(3, domains, names) == "not_searchable"

    def test_export_has_one_row_per_organization_and_a_metadata_file(self, tmp_path) -> None:
        winner = make_candidate(
            bvd_id="XX000903",
            name_via_transliteration=True,
            orbis_latin_name="Acme Latin",
            name_via_latin_name=True,
        )
        results = {
            1: make_match_result(status=MatchStatus.UNIQUE, best_candidate=winner),
            3: make_match_result(github_id=3, status=MatchStatus.NO_MATCH),
        }
        orgs = {
            1: {
                "id": 1,
                "login": "acme",
                "name": "Acme",
                "blog": "https://acme.example",
                "country_code": "DE",
            },
            3: {"id": 3, "login": "x", "name": "", "blog": "", "country_code": None},
        }
        rows = outcome_rows(results, orgs, {}, {1: "domain", 3: "not_searchable"})
        meta_path = write_outcome_export(tmp_path / "run.csv", rows, {"run_id": "r1"})

        with open(tmp_path / "run.csv", encoding="utf-8", newline="") as f:
            exported = list(csv.DictReader(f))
        assert [r["login"] for r in exported] == ["acme", "x"]
        assert exported[0]["search_path"] == "domain"
        assert exported[0]["blog"] == "https://acme.example"  # labellers' main evidence
        assert exported[0]["via_transliteration"] == "True"
        assert exported[0]["company_latin_name"] == "Acme Latin"
        assert exported[0]["via_latin_name"] == "True"
        assert list(exported[0]) == OUTCOME_COLUMNS
        assert exported[1]["bvd_id"] == ""
        assert json.loads(meta_path.read_text(encoding="utf-8")) == {"run_id": "r1"}

    def test_run_metadata_identifies_code_configuration_and_orbis_schema(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ORBIS_LATIN_NAME_COLUMN", raising=False)
        cli_args = {"limit": 1000, "logins": None, "min_name_similarity": 0.6}
        meta = build_run_metadata(
            run_id="r1",
            dry_run=True,
            orbis_schema="ORBIS_23J",
            started_at="2026-10-19T10:00:00+00:00",
            status_counts={"unique": 1},
            cli_args=cli_args,
        )

        # Everything that changes the outcome: code, uncommitted edits, configuration,
        # blog blacklist, and the options that select organizations or move thresholds.
        assert set(meta) == {
            "run_id",
            "dry_run",
            "git_commit",
            "git_dirty",
            "config_sha256",
            "blacklist_sha256",
            "cli_args",
            "orbis_schema",
            "started_at",
            "status_counts",
            "latin_name_column_used",
            "use_latin_name",
        }
        assert meta["latin_name_column_used"] is False
        assert meta["use_latin_name"] is None  # nicht übergeben
        expected = hashlib.sha256(
            (REPO / "config" / "orbis_matching.yaml").read_bytes()
        ).hexdigest()
        assert meta["config_sha256"] == expected
        blacklist = hashlib.sha256((REPO / "config" / "blog_blacklist.txt").read_bytes())
        assert meta["blacklist_sha256"] == blacklist.hexdigest()
        assert meta["cli_args"] == cli_args
        assert isinstance(meta["git_dirty"], bool)
        assert len(meta["git_commit"]) == 40


HUAWEI_ORG = {"id": 1, "login": "huawei", "name": "Huawei", "blog": "", "country_code": None}
HUAWEI_ROW = {"bvd_id": "XX900001", "name": "华为技术有限公司", "country_iso": "CN"}
YANDEX_ORG = {"id": 2, "login": "yandex", "name": "Yandex", "blog": "", "country_code": "RU"}
YANDEX_ROW = {"bvd_id": "XX900002", "name": "Яндекс", "country_iso": "RU", "latin_name": "Yandex"}


class TestLatinNameInThePipeline:
    """Lateinischer Name nur für Domain-Kandidaten (E29) und nur mit Schalter (E37)."""

    def test_domain_candidate_uses_the_latin_name(self) -> None:
        (c,) = build_match_candidates(YANDEX_ORG, [YANDEX_ROW], "yandex.example")
        assert c.orbis_latin_name == "Yandex"
        assert c.has_name_match is True

    def test_switch_off_ignores_the_latin_name(self) -> None:
        (c,) = build_match_candidates(
            YANDEX_ORG, [YANDEX_ROW], "yandex.example", use_latin_name=False
        )
        assert c.orbis_latin_name is None
        assert c.has_name_match is False

    def test_name_fallback_has_no_latin_name(self) -> None:
        (c,) = build_name_only_candidates(HUAWEI_ORG, [HUAWEI_ROW])
        assert c.orbis_latin_name is None

    def test_metadata_records_whether_the_column_was_used(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ORBIS_LATIN_NAME_COLUMN", "LATIN_NAME_TEST")
        meta = build_run_metadata(
            "r1", True, "ORBIS_23J", "2026-10-20T10:00:00+00:00", {}, use_latin_name=False
        )
        assert meta["latin_name_column_used"] is True
        # Spalte gelesen, Vergleich aber per Schalter aus (Final Review, Befund 4)
        assert meta["use_latin_name"] is False


class TestTransliterationInThePipeline:
    """Die Umschrift zählt als Namens-Treffer nur auf dem Domain-Weg (E19)."""

    def test_domain_path_counts_a_transliterated_name_match(self) -> None:
        (candidate,) = build_match_candidates(HUAWEI_ORG, [HUAWEI_ROW], "huawei.com")
        assert candidate.has_name_match is True

    @pytest.mark.parametrize(
        "orbis_name, github_name, expected",
        [
            # the transliteration matches the GitHub name -> name match, a hit
            ("Сбербанк", "Sberbank", "unique"),
            # documented limit: unidecode gives "Iandeks", similarity 0.62 < 0.75, so the
            # candidate earns only blog points and stays below the minimum score
            ("Яндекс", "Yandex", "no_match"),
        ],
    )
    def test_domain_hit_with_a_non_latin_name_through_the_real_path(
        self, orbis_name, github_name, expected
    ) -> None:
        org = {
            "id": 1,
            "login": github_name.lower(),
            "name": github_name,
            "blog": "",
            "country_code": None,
        }
        row = {"bvd_id": "XX900002", "name": orbis_name, "country_iso": "RU"}
        candidates = build_match_candidates(org, [row], f"{github_name.lower()}.example")

        result = analyze_matches({1: candidates}, config=ScoringConfig())[1]

        assert result.status.value == expected

    def test_name_fallback_does_not_use_transliteration(self) -> None:
        (candidate,) = build_name_only_candidates(HUAWEI_ORG, [HUAWEI_ROW])
        assert candidate.has_name_match is False


from github_collector.enrichment.hq_detection import (  # noqa: E402
    HQResolution,
    resolve_headquarters,
)
from github_collector.enrichment.orbis_matching_logic import (  # noqa: E402
    MatchCandidate,
    MatchResult,
    MatchStatus,
    MatchType,
    ScoringConfig,
    analyze_matches,
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
        apply_hq_signals([candidate], root_domain=None, hq_subsidiary_counts={"XX000017": 5000})
        assert candidate.has_hq_entry is True
        assert candidate.hq_subsidiary_count == 5000

    def test_missing_bvd_id_defaults_to_no_hq_entry(self) -> None:
        candidate = make_candidate(bvd_id="US999")
        apply_hq_signals([candidate], root_domain=None, hq_subsidiary_counts={"XX000017": 5000})
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
    """Tests für das Namens-Fallback-Gate (Charge-10-musdec-/Charge-5-9-Fund)."""

    def test_long_enough_term_passes(self) -> None:
        assert should_attempt_name_fallback("Musdec", threshold=5) is True

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

    def test_resolved_pending_gets_the_match_type_of_the_chosen_candidate(self) -> None:
        """Bisher blieb match_type 'pending' - der Match-Type des gewählten
        Kandidaten ging verloren (H5). Der Status bleibt PENDING (E17)."""
        winner = make_candidate(
            bvd_id="US222",
            match_type_input="name_blog",
            total_score=60,
            has_name_match=True,
            has_blog_match=True,
        )
        resolution = HQResolution(
            bvd_id="US222", country_iso="US", hq_method="HQ_TABLE", candidate=winner
        )
        result = make_match_result(best_candidate=None, match_type=MatchType.PENDING)

        apply_hq_resolution_to_match_result(result, resolution, ScoringConfig())

        assert result.status == MatchStatus.PENDING
        assert result.match_type == MatchType.COMBINED_HIGH

    def test_unresolved_pending_keeps_its_match_type(self) -> None:
        result = make_match_result(best_candidate=None, match_type=MatchType.PENDING)

        apply_hq_resolution_to_match_result(result, None)

        assert result.match_type == MatchType.PENDING


def test_invalid_latin_name_column_stops_the_run_before_any_query(monkeypatch) -> None:
    # The invalid value used to raise only when Orbis was queried or, with --export-csv, when
    # the run metadata were written at the end (final review of the Latin-name plan).
    import orbis_matching_pipeline as pipeline

    def no_query(*args, **kwargs):
        raise AssertionError("no organization may be loaded")

    monkeypatch.setenv("ORBIS_LATIN_NAME_COLUMN", "NAME; DROP")
    monkeypatch.setattr(pipeline, "fetch_organizations", no_query)

    with pytest.raises(ValueError, match="ORBIS_LATIN_NAME_COLUMN"):
        pipeline.run_pipeline(limit=None, logins=None, dry_run=True, min_name_similarity=None)
