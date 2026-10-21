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
    assert set(queries) == {"non_latin_by_country", "latin_name_column_exists"}
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
    # A range such as [A-Za-z] depends on NLS_SORT and can include accented letters; the
    # letters are therefore listed one by one (final review M8).
    query = load().build_queries("ORBIS_23J")["non_latin_by_country"]
    letters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    assert f"REGEXP_LIKE(NAME_NATIVE, '[{letters}]')" in query
    assert "A-Z" not in query and "a-z" not in query
    assert "NAME_NATIVE IS NOT NULL" in query


def test_the_company_table_is_read_once():
    # The total used to be a second full scan of the table (final review M8).
    queries = load().build_queries("ORBIS_23J").values()
    assert sum(query.count("FROM ORBIS_23J.CONTACT_INFO") for query in queries) == 1


def test_total_and_top_countries_come_from_one_result():
    rows = [("CN", 10, 8), ("DE", 5, 0), ("RU", 4, 3)]
    total, top = load().summarize(rows, top=2)
    assert total == (19, 11)
    assert top == [("CN", 10, 8), ("RU", 4, 3)]


def test_help_does_not_connect(monkeypatch, capsys):
    """Functional test 2026-10-20: `--help` started the full scan of Orbis."""
    import pytest

    module = load()

    def no_connection(*args, **kwargs):
        raise AssertionError("--help must not connect to Orbis")

    monkeypatch.setattr(module, "OrbisClient", no_connection)
    with pytest.raises(SystemExit) as exit_info:
        module.main(["--help"])
    assert exit_info.value.code == 0
    assert "Orbis" in capsys.readouterr().out
