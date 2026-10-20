"""config/orbis_matching.yaml contains only what the code reads (finding M10).

Unread sections (validation thresholds, processing sizes, phases, database settings)
made a reader assume they were applied.
"""

from github_collector.enrichment.orbis_matching_logic import (
    ScoringConfig,
    load_matching_config,
)


def test_config_has_only_what_the_code_reads():
    config = load_matching_config()

    assert set(config) <= {"scoring", "plausibility", "match_types"}
    # ScoringConfig.from_yaml reads only the score thresholds of these two Match-Types
    assert set(config["match_types"]) <= {"combined_high", "combined_medium"}
    for match_type in config["match_types"].values():
        assert set(match_type) == {"min_score"}


def test_latin_name_switch_defaults_to_on_and_can_be_turned_off():
    """Schalter für den Vergleich über den lateinischen Namen (E37)."""
    assert ScoringConfig.from_yaml({}).use_latin_name is True
    off = ScoringConfig.from_yaml({"plausibility": {"use_latin_name": False}})
    assert off.use_latin_name is False
    assert ScoringConfig.from_yaml(load_matching_config()).use_latin_name is True
    assert load_matching_config()["plausibility"]["use_latin_name"] is True
