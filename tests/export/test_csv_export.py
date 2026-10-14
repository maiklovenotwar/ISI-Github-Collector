"""CSV export streams tables in chunks and reports failed tables (K5)."""

import csv
import sys

from sqlalchemy import create_engine, insert

from github_collector.cli import export_command
from github_collector.database.database import GitHubDatabase
from github_collector.database.models import Contributor, Organization
from github_collector.export import csv_export


def _db_with_contributors(tmp_path, count):
    db = GitHubDatabase(f"sqlite:///{tmp_path / 'export.db'}")
    rows = [{"id": i, "login": f"user{i}", "type": "User"} for i in range(count, 0, -1)]
    engine = create_engine(db.db_url)
    with engine.begin() as conn:
        conn.execute(insert(Contributor.__table__), rows)
    engine.dispose()
    return db


def test_iter_rows_reads_in_chunks_ordered_by_id(tmp_path):
    db = _db_with_contributors(tmp_path, 25)
    ids = [row["id"] for row in db.iter_rows_for_export("contributors", chunk_size=10)]
    assert ids == list(range(1, 26))


def test_iter_rows_respects_limit(tmp_path):
    db = _db_with_contributors(tmp_path, 25)
    assert len(list(db.iter_rows_for_export("contributors", limit=12, chunk_size=10))) == 12


def test_export_writes_every_row_across_chunks(tmp_path):
    db = _db_with_contributors(tmp_path, 25_001)
    out = tmp_path / "out" / "contributors.csv"
    assert csv_export.export_table_to_csv(db, "contributors", str(out)) == 25_001
    with out.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 25_001
    assert rows[0]["id"] == "1" and rows[-1]["id"] == "25001"
    assert "country_code" in rows[0]


def test_empty_table_writes_header_only(tmp_path):
    db = GitHubDatabase(f"sqlite:///{tmp_path / 'empty.db'}")
    out = tmp_path / "organizations.csv"
    assert csv_export.export_table_to_csv(db, "organizations", str(out)) == 0
    header = out.read_text(encoding="utf-8").splitlines()
    assert header[0].split(",")[0] == "id" and len(header) == 1
    assert len(header[0].split(",")) == len(Organization.__table__.columns)


def test_failed_table_makes_main_return_1(tmp_path, monkeypatch):
    db = _db_with_contributors(tmp_path, 3)
    real = csv_export.export_table_to_csv

    def failing(db, table_name, output_file, limit=None):
        if table_name == "organizations":
            raise RuntimeError("disk full")
        return real(db, table_name, output_file, limit=limit)

    monkeypatch.setattr(csv_export, "export_table_to_csv", failing)
    monkeypatch.setattr(
        sys,
        "argv",
        ["export", "--db-path", db.db_url, "--output-dir", str(tmp_path / "exp")],
    )
    assert export_command.main() == 1
    assert (tmp_path / "exp" / "contributors.csv").exists()


def test_export_tables_reports_failures(tmp_path, monkeypatch):
    db = _db_with_contributors(tmp_path, 3)

    def failing(db, table_name, output_file, limit=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(csv_export, "export_table_to_csv", failing)
    assert csv_export.export_tables(db, ["contributors"], str(tmp_path)) == (0, ["contributors"])
