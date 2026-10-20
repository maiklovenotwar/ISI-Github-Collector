"""Blind, stratified validation sample from the outcome list of one run (task 7, E20).

All rows are invented.
"""

import csv
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "scripts" / "diagnostics" / "draw_orbis_validation_sample.py"


@pytest.fixture(scope="module")
def drawer():
    spec = importlib.util.spec_from_file_location("draw_orbis_validation_sample", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(login, status, bvd_id="", search_path="domain", match_type="", via="False"):
    return {
        "login": login,
        "name": login.title(),
        "blog": f"https://{login}.example",
        "github_country": "DE",
        "search_path": search_path,
        "status": status,
        "match_type": match_type,
        "bvd_id": bvd_id,
        "company_name": "Invented Co" if bvd_id else "",
        "company_country": "DE" if bvd_id else "",
        "company_city": "",
        "company_website": "",
        "score": "60" if bvd_id else "",
        "hq_method": "",
        "via_transliteration": via,
        "via_latin_name": "False",
    }


@pytest.mark.parametrize(
    "outcome, expected",
    [
        (row("a", "unique", "XX000001"), "unique"),
        (row("b", "consensus", "XX000002"), "consensus"),
        (row("c", "pending", "XX000003"), "pending_resolved"),
        (row("d", "pending"), "pending_unresolved"),
        (row("e", "no_match"), "searched_no_candidate"),
        (row("f", "no_match", search_path="not_searchable"), "not_searchable"),
    ],
)
def test_stratum_of(drawer, outcome, expected):
    assert drawer.stratum_of(outcome) == expected


def population():
    rows = [row(f"u{i}", "unique", f"XX1{i:05d}") for i in range(80)]
    rows += [row(f"c{i}", "consensus", f"XX2{i:05d}") for i in range(10)]
    rows += [row(f"n{i}", "no_match") for i in range(30)]
    rows += [row(f"t{i}", "unique", f"XX3{i:05d}", via="True") for i in range(5)]
    rows += [row(f"x{i}", "no_match", search_path="not_searchable") for i in range(40)]
    return rows


def test_same_seed_gives_the_same_sample(drawer):
    first = drawer.draw_sample(population(), seed=7, exclude_logins=set())
    second = drawer.draw_sample(population(), seed=7, exclude_logins=set())
    assert [r["login"] for r in first] == [r["login"] for r in second]


def test_sizes_are_kept_and_small_strata_are_taken_whole(drawer):
    sample = drawer.draw_sample(
        population(), sizes={"unique": 50, "consensus": 50}, seed=1, exclude_logins=set()
    )
    by_stratum = {}
    for r in sample:
        by_stratum.setdefault(r["stratum"], []).append(r)
    assert len(by_stratum["unique"]) == 50
    assert len(by_stratum["consensus"]) == 10
    assert "not_searchable" not in by_stratum  # only counted, never drawn


def test_transliterated_hits_are_oversampled(drawer):
    sample = drawer.draw_sample(population(), seed=1, exclude_logins=set())
    transliterated = [r for r in sample if r["via_transliteration"] == "True"]
    assert len(transliterated) == 5


def test_hits_through_the_latin_name_are_oversampled(drawer):
    hit = {**row("a", "unique", "XX000001"), "via_latin_name": "True"}
    assert drawer._oversample_group(hit) == "via_latin_name"
    both = {**hit, "via_transliteration": "True"}
    assert drawer._oversample_group(both) == "via_transliteration"
    assert drawer._oversample_group({**row("b", "no_match"), "via_latin_name": "True"}) is None


def test_case_order_does_not_reveal_the_stratum(drawer):
    # The blind file is numbered in sample order; grouped by stratum, the position alone
    # would tell the labeller which result the procedure produced.
    sample = drawer.draw_sample(population(), seed=1, exclude_logins=set())
    strata = [r["stratum"] for r in sample]
    changes = sum(1 for a, b in zip(strata, strata[1:]) if a != b)
    # grouped order would change stratum only len(set(strata)) - 1 times
    assert changes > len(set(strata)) - 1


def test_development_cases_are_excluded(drawer):
    sample = drawer.draw_sample(population(), seed=1, exclude_logins={"u1", "c2"})
    assert {"u1", "c2"}.isdisjoint(r["login"] for r in sample)


def test_blind_file_has_no_result_columns(drawer, tmp_path):
    sample = drawer.draw_sample(population(), seed=1, exclude_logins=set())
    blind, key = drawer.write_sample(sample, tmp_path / "round1")
    with open(blind, encoding="utf-8", newline="") as f:
        header = next(csv.reader(f))
    assert header == ["case_id", "login", "name", "blog", "github_country"]
    with open(key, encoding="utf-8", newline="") as f:
        assert "bvd_id" in next(csv.reader(f))


def test_checks_the_files_it_writes_not_only_the_stem(drawer, tmp_path):
    # The ignored folder itself as stem: <folder>_key.csv lands next to it, unignored.
    folder = REPO / "workstreams" / "orbis_matching" / "data"
    folder.mkdir(parents=True, exist_ok=True)
    export = tmp_path / "run.csv"
    with open(export, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row("a", "unique").keys()))
        writer.writeheader()
        writer.writerow(row("a", "unique", "XX000001"))
    written = [folder.with_name("data_blind.csv"), folder.with_name("data_key.csv")]
    try:
        with pytest.raises(ValueError, match="not ignored"):
            drawer.main(["--export", str(export), "--out", str(folder)])
        assert not any(path.exists() for path in written)
    finally:
        for path in written:
            if path.exists():
                path.unlink()


def test_refuses_an_output_path_git_does_not_ignore(drawer, tmp_path):
    export = tmp_path / "run.csv"
    with open(export, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row("a", "unique").keys()))
        writer.writeheader()
        writer.writerow(row("a", "unique", "XX000001"))
    with pytest.raises(ValueError, match="not ignored"):
        drawer.main(["--export", str(export), "--out", str(REPO / "sample_round1")])
