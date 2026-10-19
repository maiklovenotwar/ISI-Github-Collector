"""config/orbis_matching.yaml contains only what the code reads (finding M10).

Unread sections (validation thresholds, processing sizes, phases, database settings)
made a reader assume they were applied.
"""

from github_collector.enrichment.orbis_matching_logic import load_matching_config


def test_config_has_only_what_the_code_reads():
    config = load_matching_config()

    assert set(config) <= {"scoring", "plausibility", "match_types"}
    # ScoringConfig.from_yaml reads only the score thresholds of these two Match-Types
    assert set(config["match_types"]) <= {"combined_high", "combined_medium"}
    for match_type in config["match_types"].values():
        assert set(match_type) == {"min_score"}
