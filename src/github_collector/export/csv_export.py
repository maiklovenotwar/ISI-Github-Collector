"""
CSV-Export-Funktionalität für den GitHub Data Collector.

Dieses Modul bietet Funktionen zum Exportieren von Datenbanktabellen in CSV-Dateien.
Die Tabellen werden blockweise gelesen und gestreamt geschrieben; eine Tabelle, deren
Export scheitert, wird gemeldet (Befund K5: früher endete der Export trotzdem mit Erfolg).

Die Ausgabe enthält die Rohspalten, darunter personenbezogene Angaben (Name, E-Mail, Bio).
"""

import csv
import logging
import os
from datetime import datetime
from typing import List, Optional, Tuple

from github_collector.database.database import _MODEL_REGISTRY

logger = logging.getLogger(__name__)


def export_table_to_csv(db, table_name: str, output_file: str, limit: Optional[int] = None) -> int:
    """
    Exportiere eine Tabelle in eine CSV-Datei.

    Args:
        db: Datenbankverbindung (GitHubDatabase)
        table_name: Name der zu exportierenden Tabelle
        output_file: Pfad zur Ausgabedatei
        limit: Maximale Anzahl zu exportierender Zeilen

    Returns:
        Anzahl der exportierten Zeilen

    Raises:
        ValueError bei unbekannter Tabelle; Datenbank- und Schreibfehler werden nicht
        verschluckt.
    """
    if table_name not in _MODEL_REGISTRY:
        raise ValueError(f"Unbekannte Tabelle für Export: {table_name}")
    columns = [column.name for column in _MODEL_REGISTRY[table_name].__table__.columns]

    directory = os.path.dirname(output_file)
    if directory:
        os.makedirs(directory, exist_ok=True)

    # Erst in eine Teildatei schreiben und nur bei Erfolg umbenennen: eine abgebrochene
    # Tabelle soll nicht als vollständige CSV-Datei unter ihrem Namen liegen bleiben.
    partial_file = output_file + ".part"
    exported = 0
    try:
        with open(partial_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            for row in db.iter_rows_for_export(table_name, limit=limit):
                writer.writerow([row.get(column) for column in columns])
                exported += 1
    except BaseException:
        if os.path.exists(partial_file):
            os.remove(partial_file)
        raise
    os.replace(partial_file, output_file)

    logger.info(f"{exported} Zeilen aus Tabelle {table_name} nach {output_file} exportiert")
    return exported


def export_tables(
    db,
    tables: List[str],
    output_dir: str,
    limit: Optional[int] = None,
    with_timestamp: bool = False,
) -> Tuple[int, List[str]]:
    """
    Exportiere mehrere Tabellen in CSV-Dateien.

    Args:
        db: Datenbankverbindung
        tables: Liste der zu exportierenden Tabellen
        output_dir: Verzeichnis für die exportierten CSV-Dateien
        limit: Maximale Anzahl zu exportierender Zeilen pro Tabelle
        with_timestamp: Zeitstempel zum Dateinamen hinzufügen

    Returns:
        (Gesamtanzahl der exportierten Zeilen, Namen der fehlgeschlagenen Tabellen)
    """
    os.makedirs(output_dir, exist_ok=True)

    timestamp = ""
    if with_timestamp:
        timestamp = f"_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    total_exported = 0
    failed: List[str] = []

    for table_name in tables:
        output_file = os.path.join(output_dir, f"{table_name}{timestamp}.csv")
        try:
            total_exported += export_table_to_csv(
                db=db, table_name=table_name, output_file=output_file, limit=limit
            )
        except Exception as e:
            logger.error(f"Fehler beim Exportieren der Tabelle {table_name}: {e}")
            failed.append(table_name)

    logger.info(f"Export abgeschlossen. Insgesamt {total_exported} Zeilen exportiert.")
    if failed:
        logger.error(f"Nicht exportiert: {', '.join(failed)}")
    return total_exported, failed
