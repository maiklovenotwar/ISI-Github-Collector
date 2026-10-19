"""scripts/diagnostics/count_orbis_non_latin_names.py only reads, and only counts (task 5, M1).

No connection is opened; the queries are inspected as text.
"""

import importlib.util
import re
from pathlib import Path

SCRIPT = (
    Path(__file__).parent.parent.parent
    / "scripts"
    / "diagnostics"
    / "count_orbis_non_latin_names.py"
)


def load():
    spec = importlib.util.spec_from_file_location("count_orbis_non_latin_names", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def select_list(query: str) -> str:
    """Output columns of the outermost SELECT, with every parenthesised expression removed.

    What remains are bare columns; aggregates such as COUNT(NAME_NATIVE) disappear.
    """
    selected = re.search(r"SELECT(.*?)\bFROM\b", query, re.S | re.I).group(1)
    previous = None
    while previous != selected:
        previous, selected = selected, re.sub(r"\([^()]*\)", "", selected)
    return selected


def test_queries_are_read_only():
    queries = load().build_queries("ORBIS_23J")
    assert set(queries) == {"non_latin_total", "non_latin_by_country", "latin_name_column_exists"}
    forbidden = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE)\b", re.I)
    for query in queries.values():
        assert query.lstrip().upper().startswith("SELECT")
        assert not forbidden.search(query)


def test_queries_return_counts_only():
    # Neither company names nor column names leave Orbis, only numbers and country codes.
    for query in load().build_queries("ORBIS_23J").values():
        selected = select_list(query).upper()
        assert "NAME_NATIVE" not in selected
        assert "COLUMN_NAME" not in selected


def test_non_latin_means_no_latin_letter():
    query = load().build_queries("ORBIS_23J")["non_latin_total"]
    assert "REGEXP_LIKE(NAME_NATIVE, '[A-Za-z]')" in query
    assert "NAME_NATIVE IS NOT NULL" in query
