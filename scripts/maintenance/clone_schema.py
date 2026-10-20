#!/usr/bin/env python3
"""Kopiert das Tabellendesign einer MySQL-Datenbank in eine leere Testdatenbank.

Die Tabellen werden nicht aus den Modellen erzeugt (`create_all`), sondern mit
`SHOW CREATE TABLE` aus der Quelle übernommen. So hat die Testdatenbank dieselben Spalten,
Indizes, Fremdschlüssel und Tabellenoptionen wie die Quelle, auch alles, was Migrationen
hinzugefügt haben. Übernommen werden nur Basistabellen; Tabellen mit dem Präfix `tmp_` werden
ausgelassen. Der Zähler `AUTO_INCREMENT=<n>` der Quelle wird nicht übernommen.

Die Quelle wird nur gelesen (Session READ ONLY). Das Ziel darf nicht auf Port 3306 liegen,
nicht gleich der Quelle sein und noch keine Tabellen haben. Quelle und Ziel werden beide
ausdrücklich angegeben und nie aus DATABASE_URL abgeleitet. Ablauf der Testinstanz:
docs/usage.md, Abschnitt "Test Instance".

Usage:
    python scripts/maintenance/clone_schema.py --source-url <url> --target-url <url>
"""

import argparse
import re
import sys
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

PRODUCTION_PORT = 3306
_TABLE_COUNTER = re.compile(r"\sAUTO_INCREMENT=\d+")


def portable_ddl(create_statement: str) -> str:
    """CREATE TABLE ohne den Zähler `AUTO_INCREMENT=<n>` in den Tabellenoptionen."""
    head, sep, options = create_statement.rpartition(")")
    if not sep:
        return create_statement
    return head + sep + _TABLE_COUNTER.sub("", options)


def check_target(source_url: str, target_url: str) -> None:
    """Verweigert ein Ziel auf Port 3306 oder ein Ziel, das gleich der Quelle ist.

    Raises:
        ValueError: Wenn das Ziel nicht als Testdatenbank taugt.
    """
    source, target = make_url(source_url), make_url(target_url)
    target_port = target.port or PRODUCTION_PORT
    if target_port == PRODUCTION_PORT:
        raise ValueError(f"Ziel auf Port {PRODUCTION_PORT} verweigert: {target.host}")
    same = (source.host, source.port or PRODUCTION_PORT, source.database) == (
        target.host,
        target_port,
        target.database,
    )
    if same:
        raise ValueError("Ziel ist gleich der Quelle")


def clone_schema(
    source_url: str, target_url: str, exclude_prefixes: Tuple[str, ...] = ("tmp_",)
) -> List[str]:
    """Legt jede Basistabelle der Quelle im leeren Ziel an; gibt die Tabellennamen zurück.

    Raises:
        ValueError: Wenn das Ziel unzulässig ist oder schon Tabellen hat.
    """
    check_target(source_url, target_url)
    with create_engine(source_url).connect() as source:
        source.execute(text("SET SESSION TRANSACTION READ ONLY"))
        tables = [
            row[0]
            for row in source.execute(text("SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'"))
            if not row[0].startswith(exclude_prefixes)
        ]
        statements = [
            portable_ddl(source.execute(text(f"SHOW CREATE TABLE `{name}`")).fetchone()[1])
            for name in tables
        ]

    with create_engine(target_url).begin() as target:
        existing = target.execute(text("SHOW TABLES")).fetchall()
        if existing:
            raise ValueError(f"Ziel hat schon {len(existing)} Tabellen")
        # Gilt nur für diese Verbindung: Tabellen dürfen sich vor ihrer Anlage referenzieren.
        target.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for statement in statements:
            target.execute(text(statement))
        target.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    return tables


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source-url", required=True, help="SQLAlchemy-URL der Quelle")
    parser.add_argument(
        "--target-url", required=True, help="SQLAlchemy-URL der leeren Testdatenbank"
    )
    args = parser.parse_args(argv)
    try:
        tables = clone_schema(args.source_url, args.target_url)
    except ValueError as e:
        print(f"Abbruch: {e}", file=sys.stderr)
        return 1
    print(f"{len(tables)} Tabellen angelegt: {', '.join(tables)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
