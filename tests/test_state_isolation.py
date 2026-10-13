"""The test suite must never touch the state file of a real collection."""

from pathlib import Path

from github_collector import config
from github_collector.repository_collector import CollectionState

REAL_STATE_FILE = Path(__file__).parent.parent / "data" / "collection_state.json"


def test_default_state_file_is_redirected_during_tests(tmp_path):
    assert Path(config.STATE_CONFIG["file"]).resolve() != REAL_STATE_FILE.resolve()
    assert tmp_path in Path(config.STATE_CONFIG["file"]).parents


def test_state_created_without_a_path_does_not_use_the_real_file():
    state = CollectionState()

    assert Path(state.state_file).resolve() != REAL_STATE_FILE.resolve()
