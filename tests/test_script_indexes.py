"""Keeps the script overviews in step with the scripts that exist.

scripts/README.md and workstreams/README.md list every script with a one-line purpose.
A script that is added, renamed or removed without the overview being updated fails here.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


def _tracked_python_files(folder: str) -> set:
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    result = subprocess.run(
        ["git", "ls-files", folder], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return {
        line[len(folder) + 1 :]
        for line in result.stdout.splitlines()
        if line.endswith(".py") and not line.endswith("__init__.py")
    }


def _listed_scripts(index: str) -> list:
    text = (REPO_ROOT / index).read_text(encoding="utf-8")
    return re.findall(r"^\| `([\w/]+\.py)` \|", text, flags=re.MULTILINE)


@pytest.mark.parametrize("folder", ["scripts", "workstreams"])
def test_index_lists_exactly_the_tracked_scripts(folder):
    listed = _listed_scripts(f"{folder}/README.md")

    assert sorted(listed) == sorted(_tracked_python_files(folder))


@pytest.mark.parametrize("folder", ["scripts", "workstreams"])
def test_every_listed_script_has_a_purpose(folder):
    text = (REPO_ROOT / folder / "README.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| `[\w/]+\.py` \| ([^|]*) \|", text, flags=re.MULTILINE)

    assert rows
    assert all(len(purpose.strip()) > 20 for purpose in rows)
