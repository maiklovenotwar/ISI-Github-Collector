"""No identifiers from the licensed Orbis database in tracked files (plan task 0, E24).

BvD identifiers have the form of a two- to four-letter prefix followed by six to twelve
digits, sometimes with a trailing letter. Tests use placeholders of the form `XX000001`.
"""

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

BVD_LIKE = re.compile(r"\b(?!XX)[A-Z]{2,4}[0-9]{6,12}[A-Z]?\b")

# Deliberate exceptions (path, identifier). Kept empty: every hit is replaced.
ALLOWED: set = set()


def tracked_files():
    out = subprocess.run(
        ["git", "ls-files", "*.py", "*.md", "*.yaml", "*.yml", "*.sql", "*.html"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [REPO / line for line in out.splitlines() if line]


def test_no_bvd_like_identifiers_in_tracked_files():
    hits = []
    for path in tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for match in BVD_LIKE.finditer(line):
                rel = path.relative_to(REPO).as_posix()
                if (rel, match.group()) not in ALLOWED:
                    hits.append(f"{rel}:{number}: {match.group()}")
    assert not hits, "BvD-like identifiers found:\n" + "\n".join(hits)
