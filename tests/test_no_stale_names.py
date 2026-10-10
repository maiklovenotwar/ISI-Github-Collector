"""Guards against references to folders and scripts that have been renamed.

Paths in comments and documentation are not checked by any import, so a rename can leave
them pointing nowhere. This test reads every tracked text file.
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent

OLD_NAMES = [
    "github_micro_network_data",
    "github_orbis_matching",
    "github_entity_classification",
    "run_collection_pipeline",
    "create_contributor_stubs",
    "validate_against_batches",
    "local_llm_compare",
    "classify_full_db",
    "classify_against_goldstandard",
    "merge_goldstandard",
    "build_goldstandard_chart",
    "local_llm_comparison",
    "full_db_classification",
]
# The changelog records the renames, .gitignore keeps the removed folder ignored.
EXEMPT = {"CHANGELOG.md", ".gitignore", "tests/test_no_stale_names.py"}


def _tracked_files():
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    result = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return [line for line in result.stdout.splitlines() if line not in EXEMPT]


def test_no_tracked_text_file_uses_an_old_name():
    hits = []
    for name in _tracked_files():
        try:
            text = (REPO_ROOT / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        hits.extend(f"{name}: {old}" for old in OLD_NAMES if old in text)

    assert hits == []
