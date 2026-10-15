"""Engine-Erstellung für lang laufende Skripte."""

from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ArgumentError

from github_collector import config


def redact_db_url(db_url) -> str:
    """Datenbank-URL ohne Passwort, für Log und Konsole (Befund K13).

    Ein Wert, der keine gültige URL ist, wird nicht wiedergegeben, weil er das Passwort
    enthalten könnte.
    """
    try:
        return make_url(str(db_url)).render_as_string(hide_password=True)
    except (ArgumentError, ValueError):
        return "<keine gültige Datenbank-URL>"


def make_engine(db_url: Optional[str] = None) -> Engine:
    """Erzeugt eine Engine mit Reconnect-Handling für lang laufende Prozesse.

    Ohne pool_pre_ping/pool_recycle stirbt eine mehrstündige Sammlung oder Anreicherung an
    der ersten stillgelegten Verbindung (VPN-Aussetzer, DB wait_timeout) mit
    'MySQL Connection not available'.

    Args:
        db_url: SQLAlchemy-URL; ohne Angabe wird config.DB_URL zum Zeitpunkt des Aufrufs
            verwendet.
    """
    url = db_url or str(config.DB_URL)
    if url.startswith("sqlite://"):
        return create_engine(url)
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=config.DB_CONFIG["pool_recycle"],
        pool_timeout=config.DB_CONFIG["pool_timeout"],
    )
