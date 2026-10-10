"""Unit Tests für orbis_matching_logic.py.

Bis zu diesem Commit hatte dieses Modul keine Testabdeckung. Die Tests decken
sowohl die bestehende Scoring-/Konsens-Logik ab (Regression) als auch die
neuen Namens-Plausibilitäts- und BVDID-Filial-Dedup-Fixes, die direkt aus der
Chargen-Dokumentation in workstreams/orbis_matching/docs/batches/
abgeleitet sind (siehe Verweise in den einzelnen Tests).
"""

import logging

from github_collector.enrichment.orbis_matching_logic import (
    MatchCandidate,
    MatchStatus,
    MatchType,
    ScoringConfig,
    _calculate_name_similarity_detailed,
    analyze_matches,
    calculate_country_code_adjustment,
    calculate_name_similarity,
    classify_match_type,
    determine_match_status,
    filter_implausible_candidates,
    has_exact_login_substring,
    normalize_bvd_id_base,
    score_candidate,
    select_branch_representative,
)


def make_candidate(**overrides) -> MatchCandidate:
    """Baue einen MatchCandidate mit sinnvollen Defaults für Tests."""
    defaults = {
        "github_id": 1,
        "login": "acme",
        "match_value": "acme",
        "match_type_input": "name_blog",
        "name_length": 4,
        "has_name_match": True,
        "has_blog_match": True,
        "bvd_id": "DE12345678",
        "orbis_name": "Acme",
        "orbis_country": "DE",
    }
    defaults.update(overrides)
    return MatchCandidate(**defaults)


class TestCalculateNameSimilarity:
    """Tests für die neue Namens-Ähnlichkeitsprüfung."""

    def test_exact_match_returns_one(self) -> None:
        """Identischer (normalisierter) Name ergibt Ähnlichkeit 1.0."""
        assert calculate_name_similarity("Acme Inc", "Acme Inc", "acme") == 1.0

    def test_completely_unrelated_names_low_similarity(self) -> None:
        """Fund aus Charge 01: 'Clear Peak' vs. 'OpenAI' - komplett unähnlich.

        Muss unter dem Default-Schwellwert (0.5, siehe ScoringConfig) liegen,
        damit filter_implausible_candidates() diesen Fall tatsächlich verwirft.
        """
        similarity = calculate_name_similarity("Clear Peak", "OpenAI", "openai")
        assert similarity < 0.5

    def test_login_more_similar_than_name(self) -> None:
        """Fund aus Charge 04 (touchmedia360): Login kann präziser sein als Name.

        Siehe workstreams/orbis_matching/docs/batches/batch04_review.md.
        """
        orbis_name = "Touch Media 360 Services SRL"
        similarity_via_login = calculate_name_similarity(orbis_name, "Touch Media", "touchmedia360")
        similarity_via_name_only = calculate_name_similarity(orbis_name, "Touch Media", "")
        assert similarity_via_login > similarity_via_name_only

    def test_empty_orbis_name_returns_zero(self) -> None:
        """Kein Orbis-Name zum Vergleich -> 0.0 (nicht beurteilbar)."""
        assert calculate_name_similarity("", "Acme", "acme") == 0.0

    def test_no_github_signal_returns_zero(self) -> None:
        """Weder Name noch Login vorhanden -> 0.0."""
        assert calculate_name_similarity("Acme", None, "") == 0.0

    def test_case_and_punctuation_insensitive(self) -> None:
        """Groß-/Kleinschreibung und Satzzeichen beeinflussen die Ähnlichkeit nicht."""
        assert calculate_name_similarity("ACME, Inc.", "acme inc", "acme-inc") == 1.0

    def test_detailed_flags_containment_for_coincidental_substring(self) -> None:
        """Fund Charge 11: 'STUDEC' ist eine reine Zeichenketten-Koinzidenz mit
        den ersten 6 Buchstaben von 'Studecorators' (eigentlich 'Stu'+
        'Decorators') - muss als via_containment=True erkannt werden, damit
        score_candidate() den gezielten Malus anwenden kann."""
        similarity, via_containment = _calculate_name_similarity_detailed(
            "Studecorators", "STUDEC", "studec"
        )
        assert similarity > 0.75
        assert via_containment is True

    def test_detailed_does_not_flag_exact_match_as_containment(self) -> None:
        """Ein exakter Treffer ist keine Enthaltensein-Koinzidenz, auch wenn er
        technisch durch denselben Code-Zweig läuft (String enthält sich selbst)."""
        _, via_containment = _calculate_name_similarity_detailed("Studec", "STUDEC", "studec")
        assert via_containment is False

    def test_detailed_does_not_flag_sequence_matcher_path(self) -> None:
        """Wenn keine Teilstring-Beziehung vorliegt, greift SequenceMatcher -
        via_containment muss False sein, unabhängig von der Ähnlichkeit."""
        _, via_containment = _calculate_name_similarity_detailed("Clear Peak", "OpenAI", "openai")
        assert via_containment is False


class TestHasExactLoginSubstring:
    """Tests für has_exact_login_substring() (Login-Spezifitäts-Signal)."""

    def test_login_substring_within_longer_name(self) -> None:
        """Ursprünglicher Fund (Charge 04): 'touchmedia360' als Substring in
        einem deutlich längeren Namen ist ein starkes, spezifisches Signal."""
        assert has_exact_login_substring("Touch Media 360 Services SRL", "touchmedia360")

    def test_identical_login_and_name_is_not_specific(self) -> None:
        """Fund Charge 11 (otoci): Login und Orbis-Name sind normalisiert
        identisch ('otoci' == 'Otoci') - das ist trivial, aber keine
        zusätzliche Spezifität und darf nicht als starkes Signal zählen,
        sonst wird der PENDING-Schutz für Kurzname-Zufallstreffer
        (determine_match_status) ausgehebelt."""
        assert not has_exact_login_substring("Otoci", "otoci")

    def test_login_shorter_than_minimum_length(self) -> None:
        assert not has_exact_login_substring("Acme Corp Ltd", "ab", min_login_length=5)

    def test_login_not_present_at_all(self) -> None:
        assert not has_exact_login_substring("Acme Corporation", "unrelated")


class TestFilterImplausibleCandidates:
    """Tests für filter_implausible_candidates (Namens-Gate vor jedem Signal)."""

    def test_plausible_candidate_kept(self) -> None:
        candidates = [make_candidate(orbis_name="Acme Corporation", github_name="Acme")]
        result = filter_implausible_candidates(candidates, min_similarity=0.5)
        assert len(result) == 1

    def test_implausible_candidate_rejected(self) -> None:
        """Fund aus Charge 01: 'github.com' Domain-Treffer auf unbeteiligte Firma."""
        candidates = [
            make_candidate(
                login="github",
                github_name="GitHub",
                orbis_name="常州平纳精密机械配件厂",
                bvd_id="CN9459133366",
            )
        ]
        result = filter_implausible_candidates(candidates, min_similarity=0.5)
        assert result == []

    def test_candidate_without_orbis_name_passed_through(self) -> None:
        """Kandidaten ohne orbis_name sind nicht beurteilbar und werden durchgelassen."""
        candidates = [make_candidate(orbis_name="", github_name="Acme")]
        result = filter_implausible_candidates(candidates, min_similarity=0.5)
        assert len(result) == 1

    def test_sets_name_similarity_on_all_candidates(self) -> None:
        """name_similarity wird auf JEDEM Kandidaten gesetzt, auch verworfenen."""
        candidates = [make_candidate(orbis_name="Acme Corporation", github_name="Acme")]
        filter_implausible_candidates(candidates, min_similarity=0.5)
        assert candidates[0].name_similarity > 0.5

    def test_rejection_is_logged(self, caplog: "logging.LogCaptureFixture") -> None:
        candidates = [make_candidate(github_name="OpenAI", login="openai", orbis_name="Clear Peak")]
        with caplog.at_level(logging.DEBUG):
            filter_implausible_candidates(candidates, min_similarity=0.5)
        assert "verworfen" in caplog.text


class TestNormalizeBvdIdBase:
    """Tests für die BVDID-Filial-Suffix-Normalisierung."""

    def test_strips_b_suffix(self) -> None:
        """Fund: Airbus/Atlassian nutzen '-B0001'/'-B0002'-Filial-Suffixe."""
        assert normalize_bvd_id_base("AU102443916-B0002") == "AU102443916"

    def test_strips_numeric_suffix(self) -> None:
        """Fund: Schlothauer & Wauer nutzt '-1002' bis '-1007'-Filial-Suffixe."""
        assert normalize_bvd_id_base("DE2011435329-1002") == "DE2011435329"

    def test_plain_id_unchanged(self) -> None:
        assert normalize_bvd_id_base("DE2011435329") == "DE2011435329"

    def test_empty_id_unchanged(self) -> None:
        assert normalize_bvd_id_base("") == ""

    def test_short_numeric_suffix_not_stripped(self) -> None:
        """Ein einzelnes Zeichen nach '-' ist kein bekanntes Filial-Muster."""
        assert normalize_bvd_id_base("US*190521532413-9") == "US*190521532413-9"


class TestSelectBranchRepresentative:
    """Tests für die Filial-Dedup vor der Konsens-Erkennung."""

    def test_unsuffixed_variant_preferred(self) -> None:
        """Fund aus Charge 01/04: unsuffigierte Basis-BVDID ist der Repräsentant,
        auch wenn Filial-Suffixe existieren."""
        base = make_candidate(bvd_id="DE2011435329", orbis_country="DE")
        branch = make_candidate(bvd_id="DE2011435329-1002", orbis_country="DE")
        score_candidate(base, ScoringConfig())
        score_candidate(branch, ScoringConfig())

        result = select_branch_representative([base, branch])

        assert len(result) == 1
        assert result[0].bvd_id == "DE2011435329"

    def test_fallback_to_highest_score_without_unsuffixed(self) -> None:
        """Ohne unsuffigierte Variante wird der Kandidat mit dem höchsten Score gewählt."""
        low = make_candidate(bvd_id="DE12345678-1001", total_score=10)
        high = make_candidate(bvd_id="DE12345678-1002", total_score=40)

        result = select_branch_representative([low, high])

        assert len(result) == 1
        assert result[0].bvd_id == "DE12345678-1002"

    def test_unrelated_companies_not_merged(self) -> None:
        """Unterschiedliche Basis-BVDIDs bleiben getrennte Kandidaten."""
        a = make_candidate(bvd_id="DE11111111")
        b = make_candidate(bvd_id="US22222222")

        result = select_branch_representative([a, b])

        assert {c.bvd_id for c in result} == {"DE11111111", "US22222222"}


class TestScoreCandidate:
    """Regressionstests für das bestehende Scoring plus neuer Generic-Name-Malus."""

    def test_name_and_blog_combination_bonus(self) -> None:
        config = ScoringConfig()
        candidate = make_candidate(name_length=12, has_name_match=True, has_blog_match=True)

        score_candidate(candidate, config)

        assert candidate.name_score == config.name_weight_long
        assert candidate.blog_score == config.blog_score
        assert candidate.combination_bonus == config.combination_bonus
        assert candidate.total_score == (
            config.name_weight_long + config.blog_score + config.combination_bonus
        )

    def test_blog_only_no_combination_bonus(self) -> None:
        config = ScoringConfig()
        candidate = make_candidate(has_name_match=False, has_blog_match=True, name_length=0)

        score_candidate(candidate, config)

        assert candidate.name_score == 0
        assert candidate.combination_bonus == 0
        assert candidate.total_score == config.blog_score

    def test_generic_short_name_without_blog_gets_malus(self) -> None:
        """Fund aus Charge 05-09: 'FALS'/'F3X' ohne Blog-Bestätigung sind unzuverlässig."""
        config = ScoringConfig()
        candidate = make_candidate(
            name_length=4, has_name_match=True, has_blog_match=False, orbis_name="FALS"
        )

        score_candidate(candidate, config)

        expected_before_malus = config.name_weight_short
        assert candidate.total_score == max(0, expected_before_malus - config.generic_name_malus)

    def test_generic_short_name_with_blog_no_malus(self) -> None:
        """Blog-Bestätigung rettet einen kurzen Namen vor dem Malus."""
        config = ScoringConfig()
        candidate = make_candidate(name_length=4, has_name_match=True, has_blog_match=True)

        score_candidate(candidate, config)

        assert candidate.total_score == (
            config.name_weight_short + config.blog_score + config.combination_bonus
        )

    def test_score_never_negative(self) -> None:
        config = ScoringConfig(generic_name_malus=1000)
        candidate = make_candidate(name_length=3, has_name_match=True, has_blog_match=False)

        score_candidate(candidate, config)

        assert candidate.total_score == 0

    def test_containment_only_match_without_corroboration_gets_malus(self) -> None:
        """Fund Charge 11: 'Studecorators' (unbeteiligte UK-Firma) erreicht rein
        zufällig 0.838 Ähnlichkeit zu 'STUDEC' über Teilstring-Enthaltensein
        (keine Wortgrenze im Orbis-Namen erkennbar). Ohne Blog- oder
        Länder-Bestätigung wird das gemalust statt voll vertraut."""
        config = ScoringConfig()
        candidate = make_candidate(
            name_length=6,
            has_name_match=True,
            has_blog_match=False,
            name_similarity_via_containment=True,
            github_country=None,
            orbis_country="GB",
        )

        score_candidate(candidate, config)

        expected_before_malus = config.name_weight_short
        assert candidate.total_score == max(
            0, expected_before_malus - config.containment_only_malus
        )

    def test_containment_match_with_blog_no_malus(self) -> None:
        """Domain-bestätigte Kandidaten (has_blog_match=True) bekommen den
        Malus nie - betrifft strukturell nur den Namens-Fallback-Pfad."""
        config = ScoringConfig()
        candidate = make_candidate(
            name_length=6,
            has_name_match=True,
            has_blog_match=True,
            name_similarity_via_containment=True,
        )

        score_candidate(candidate, config)

        assert candidate.total_score == (
            config.name_weight_short + config.blog_score + config.combination_bonus
        )

    def test_containment_match_with_country_match_no_malus(self) -> None:
        """Länder-Übereinstimmung reicht als Bestätigung, um den Malus zu vermeiden."""
        config = ScoringConfig()
        candidate = make_candidate(
            name_length=6,
            has_name_match=True,
            has_blog_match=False,
            name_similarity_via_containment=True,
            github_country="GB",
            orbis_country="GB",
        )

        score_candidate(candidate, config)

        assert candidate.total_score == config.name_weight_short

    def test_exact_match_never_gets_containment_malus(self) -> None:
        """Ein exakter Namens-Treffer (z.B. 'Studec' vs. 'STUDEC') ist keine
        Enthaltensein-Koinzidenz und darf nie gemalust werden."""
        config = ScoringConfig()
        candidate = make_candidate(
            name_length=6,
            has_name_match=True,
            has_blog_match=False,
            name_similarity_via_containment=False,
        )

        score_candidate(candidate, config)

        assert candidate.total_score == config.name_weight_short


class TestDetermineMatchStatus:
    """Regressionstests für die Multi-Hit-Konsens-Erkennung."""

    def test_single_candidate_is_unique(self) -> None:
        candidates = [make_candidate(total_score=40)]
        assert determine_match_status(candidates, min_score=30) == MatchStatus.UNIQUE

    def test_multiple_candidates_same_country_is_consensus(self) -> None:
        candidates = [
            make_candidate(bvd_id="DE111", orbis_country="DE", total_score=40),
            make_candidate(bvd_id="DE222", orbis_country="DE", total_score=35),
        ]
        assert determine_match_status(candidates, min_score=30) == MatchStatus.CONSENSUS

    def test_multiple_candidates_different_countries_is_pending(self) -> None:
        candidates = [
            make_candidate(bvd_id="DE111", orbis_country="DE", total_score=40),
            make_candidate(bvd_id="US222", orbis_country="US", total_score=35),
        ]
        assert determine_match_status(candidates, min_score=30) == MatchStatus.PENDING

    def test_no_candidate_above_min_score_is_no_match(self) -> None:
        candidates = [make_candidate(total_score=10)]
        assert determine_match_status(candidates, min_score=30) == MatchStatus.NO_MATCH

    def test_name_only_candidate_uses_lower_threshold_but_stays_pending(self) -> None:
        """Fund aus Charge 10 (studec): name_only-Kandidaten haben nie
        blog_score/combination_bonus und erreichen den normalen min_score=30
        für kurze Namen (name_weights.short=10) strukturell nie - brauchen
        eine eigene, niedrigere Schwelle (name_only_min_score), um überhaupt
        als `valid` zu gelten. Aber (Fund aus Charge 11, otoci/BeholderDEV):
        ein unbestätigter name_only-Treffer darf trotzdem nie UNIQUE werden,
        selbst wenn er (zufällig) der einzige Kandidat ist - sonst wirkt ein
        einzelner Zufallstreffer auf ein generisches Wort fälschlich
        hochsicher."""
        candidates = [make_candidate(match_type_input="name_only", total_score=10)]
        assert (
            determine_match_status(candidates, min_score=30, name_only_min_score=10)
            == MatchStatus.PENDING
        )

    def test_name_only_candidate_without_override_still_uses_min_score(self) -> None:
        """Rückwärtskompatibilität: ohne name_only_min_score gilt weiterhin min_score für alle."""
        candidates = [make_candidate(match_type_input="name_only", total_score=10)]
        assert determine_match_status(candidates, min_score=30) == MatchStatus.NO_MATCH

    def test_name_only_candidate_with_login_exact_match_can_be_unique(self) -> None:
        """Ausnahme: ein exakter Login-Substring (siehe has_exact_login_substring())
        ist ein eigenständig starkes Signal und darf auch bei name_only zu
        UNIQUE führen (z.B. touchmedia360-artige Fälle über den Namens-Fallback)."""
        candidates = [
            make_candidate(match_type_input="name_only", total_score=10, login_exact_match=True)
        ]
        assert (
            determine_match_status(candidates, min_score=30, name_only_min_score=10)
            == MatchStatus.UNIQUE
        )

    def test_name_only_noise_with_multiple_same_country_stays_pending(self) -> None:
        """Fund Charge 11: auch bei mehreren name_only-Treffern im selben Land
        (die sonst CONSENSUS ergäben) darf ohne Bestätigung keine hohe
        Konfidenz entstehen - reine Namensgleichheit ist kein Landeskonsens."""
        candidates = [
            make_candidate(
                bvd_id="US111", orbis_country="US", match_type_input="name_only", total_score=10
            ),
            make_candidate(
                bvd_id="US222", orbis_country="US", match_type_input="name_only", total_score=10
            ),
        ]
        assert (
            determine_match_status(candidates, min_score=30, name_only_min_score=10)
            == MatchStatus.PENDING
        )


class TestClassifyMatchType:
    """Regressionstests für die Match-Type-Klassifikation."""

    def test_high_score(self) -> None:
        config = ScoringConfig()
        candidate = make_candidate(match_type_input="name_blog", total_score=70)
        assert classify_match_type(candidate, config) == MatchType.COMBINED_HIGH

    def test_medium_score(self) -> None:
        config = ScoringConfig()
        candidate = make_candidate(match_type_input="name_blog", total_score=45)
        assert classify_match_type(candidate, config) == MatchType.COMBINED_MEDIUM

    def test_low_score(self) -> None:
        config = ScoringConfig()
        candidate = make_candidate(match_type_input="name_blog", total_score=32)
        assert classify_match_type(candidate, config) == MatchType.COMBINED_LOW

    def test_below_min_score_is_pending(self) -> None:
        """Für name_blog-Kandidaten bleibt PENDING der Fallback unter min_score.

        In der Praxis unerreichbar (meets_min_score() verlangt für
        "name_blog" bereits min_score), aber als Sicherheitsnetz getestet.
        """
        config = ScoringConfig()
        candidate = make_candidate(match_type_input="name_blog", total_score=10)
        assert classify_match_type(candidate, config) == MatchType.PENDING

    def test_name_only_candidate_is_never_pending_regardless_of_score(self) -> None:
        """Regressionstest für den name_only/pending-Bug (siehe CONTEXT.md):

        Ein einzelner, eindeutiger name_only-Treffer (z.B. via
        login_exact_match als UNIQUE bestätigt, siehe
        TestDetermineMatchStatus.test_name_only_candidate_with_login_exact_match_can_be_unique)
        hatte vorher fälschlich MatchType.PENDING bekommen, weil
        classify_match_type() nur den (für name_only irrelevanten) combined_*
        Score-Schwellwert kannte statt den eigens dafür vorgesehenen
        MatchType.NAME_ONLY zu vergeben.
        """
        config = ScoringConfig()
        candidate = make_candidate(match_type_input="name_only", total_score=20)
        assert classify_match_type(candidate, config) == MatchType.NAME_ONLY

    def test_login_fallback_candidate_is_classified_as_login_fallback(self) -> None:
        config = ScoringConfig()
        candidate = make_candidate(match_type_input="login_fallback", total_score=15)
        assert classify_match_type(candidate, config) == MatchType.LOGIN_FALLBACK


class TestCalculateCountryCodeAdjustment:
    """Regressionstests für den Country-Code Bonus/Malus."""

    def test_exact_match_bonus(self) -> None:
        assert calculate_country_code_adjustment("DE", "DE") == 15

    def test_partial_match_in_all_countries(self) -> None:
        assert calculate_country_code_adjustment("DE", "US", all_orbis_countries={"DE", "US"}) == 5

    def test_no_match_penalty(self) -> None:
        assert calculate_country_code_adjustment("DE", "US", all_orbis_countries={"US"}) == -5

    def test_no_github_country_returns_zero(self) -> None:
        assert calculate_country_code_adjustment(None, "US") == 0


class TestAnalyzeMatches:
    """End-to-End-Tests für die Gesamt-Pipeline (Namens-Gate -> Scoring -> Dedup -> Status)."""

    def test_noise_candidate_filtered_before_status(self) -> None:
        """Fund aus Charge 01: unter mehreren Domain-Treffern darf ein
        namensfremder Kandidat nicht die Konsens-Erkennung verfälschen."""
        real_match = make_candidate(
            github_id=1,
            login="openai",
            github_name="OpenAI",
            bvd_id="US310437293L",
            orbis_name="Openai INC",
            orbis_country="US",
            has_name_match=True,
            has_blog_match=True,
            name_length=10,
        )
        # has_name_match=True ist hier nur Testdaten-Realismus für den Score
        # (echte Zeile hätte via Domain UND Namensähnlichkeit einen Treffer);
        # entscheidend ist, dass name_similarity zu "OpenAI" niedrig ist und
        # der Kandidat deshalb schon VOR dem Scoring verworfen wird.
        noise = make_candidate(
            github_id=1,
            login="openai",
            github_name="OpenAI",
            bvd_id="US*190521532413",
            orbis_name="Clear Peak",
            orbis_country="US",
            has_name_match=True,
            has_blog_match=True,
            name_length=10,
        )

        results = analyze_matches({1: [real_match, noise]})

        result = results[1]
        assert result.status == MatchStatus.UNIQUE
        assert result.best_candidate.bvd_id == "US310437293L"

    def test_branch_duplicates_resolved_as_unique(self) -> None:
        """Fund aus Charge 04: Basis-BVDID + Filial-Suffix sind EINE Firma,
        nicht ein Multi-Hit-Konflikt (Schlothauer & Wauer Fall)."""
        base = make_candidate(
            github_id=2,
            login="schlothauer-wauer",
            github_name="Schlothauer & Wauer GmbH",
            bvd_id="DE2011435329",
            orbis_name="Schlothauer & Wauer Ingenieurgesellschaft",
            orbis_country="DE",
        )
        branch = make_candidate(
            github_id=2,
            login="schlothauer-wauer",
            github_name="Schlothauer & Wauer GmbH",
            bvd_id="DE2011435329-1002",
            orbis_name="Schlothauer & Wauer Ingenieurgesellschaft",
            orbis_country="DE",
        )

        results = analyze_matches({2: [base, branch]})

        result = results[2]
        assert result.status == MatchStatus.UNIQUE
        assert result.best_candidate.bvd_id == "DE2011435329"

    def test_no_candidates_yields_no_match(self) -> None:
        results = analyze_matches({3: []})
        assert results[3].status == MatchStatus.NO_MATCH
        assert results[3].match_type is None

    def test_no_candidate_above_threshold_yields_no_match_type_none(self) -> None:
        """Regressionstest: status=NO_MATCH (kein Kandidat erreicht die
        Score-Schwelle) darf keinen Match-Type vortäuschen. Vorher bekam
        dieser Fall fälschlich match_type=PENDING - "kein Kandidat" und
        "mehrere uneinige Kandidaten" sind aber fachlich unterschiedliche
        Situationen (siehe CONTEXT.md, Match-Status/Match-Type)."""
        # has_blog_match=False + kurzer Name (4 Zeichen) -> name_score=10,
        # generic_name_malus=15 zieht den Score auf 0 (weit unter min_score).
        candidate = make_candidate(has_blog_match=False)
        config = ScoringConfig(min_score=30)

        results = analyze_matches({5: [candidate]}, config)

        result = results[5]
        assert result.status == MatchStatus.NO_MATCH
        assert result.match_type is None

    def test_unique_name_only_match_gets_name_only_type_not_pending(self) -> None:
        """End-to-End-Regressionstest für den name_only/pending-Bug.

        Ein Login, der wortwörtlich im Orbis-Namen steckt (löst
        login_exact_match via has_exact_login_substring() aus), aber ohne
        Blog-Signal - erreicht dadurch nur einen Score von 20 (name_score
        "medium" für 8 Zeichen, kein Blog-/Kombinationsbonus, kein Malus
        dank country_confirms=True). Das liegt unter dem normalen min_score
        (30), aber über der eigens für name_only/login_fallback gedachten
        name_only_min_score (10) - der Kandidat ist also `valid` und (dank
        login_exact_match als einziger Kandidat) UNIQUE. Vorher bekam dieser
        Fall trotz UNIQUE-Status fälschlich match_type=PENDING statt des
        dafür vorgesehenen NAME_ONLY (siehe CONTEXT.md).
        """
        candidate = make_candidate(
            github_id=4,
            login="acme1234",
            match_type_input="name_only",
            has_blog_match=False,
            name_length=8,
            orbis_name="Acme1234 Solutions GmbH",
            github_country="US",
            orbis_country="US",
        )
        config = ScoringConfig(min_score=30, name_only_min_score=10)

        results = analyze_matches({4: [candidate]}, config)

        result = results[4]
        assert result.best_candidate is not None
        assert result.best_candidate.total_score == 20
        assert result.status == MatchStatus.UNIQUE
        assert result.match_type == MatchType.NAME_ONLY
