"""No identifiers from the licensed Orbis database in tracked files (plan task 0, E24).

BvD identifiers have the form of a two- to four-letter prefix followed by six to twelve
digits, sometimes with a trailing letter. Tests use placeholders of the form `XX000001`.
"""

import re
import subprocess
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

BVD_LIKE = re.compile(r"\b(?!XX)[A-Z]{2,4}[0-9]{6,12}[A-Z]?\b")

# Deliberate exceptions (path, identifier). Kept empty: every hit is replaced.
ALLOWED: set = set()


def tracked_files():
    # Every tracked file; binary files are skipped when they cannot be read as text.
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [REPO / line for line in out.splitlines() if line]


def file_text(path: Path):
    """Text of a file; for Office files the text of their XML parts; None for other binaries."""
    if path.suffix in {".xlsx", ".docx", ".pptx"}:
        with zipfile.ZipFile(path) as archive:
            return "\n".join(
                archive.read(name).decode("utf-8", "replace")
                for name in archive.namelist()
                if name.endswith(".xml")
            )
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError):
        return None


def test_no_bvd_like_identifiers_in_tracked_files():
    hits = []
    for path in tracked_files():
        text = file_text(path)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for match in BVD_LIKE.finditer(line):
                rel = path.relative_to(REPO).as_posix()
                if (rel, match.group()) not in ALLOWED:
                    hits.append(f"{rel}:{number}: {match.group()}")
    assert not hits, "BvD-like identifiers found:\n" + "\n".join(hits)


def test_every_tracked_text_file_is_searched():
    # The guard used to search only .py, .md, .yaml, .yml, .sql and .html files.
    suffixes = {path.suffix for path in tracked_files()}
    assert {".txt", ".csv", ".toml", ".json", ".xlsx"} <= suffixes


def test_office_files_are_read_as_text():
    sheet = next(path for path in tracked_files() if path.suffix == ".xlsx")
    assert "<" in file_text(sheet)
