"""CLI-Modul für die Repository-Sammlung.

Dieses Modul implementiert die Kommandozeilenschnittstelle für die Repository-Sammlung
und stellt Funktionen für die interaktive und nicht-interaktive Ausführung bereit.
"""

import argparse
import sys
from datetime import datetime, timedelta
from typing import Optional

from dateutil import tz

from github_collector import config
from github_collector.api.github_api import GitHubAPI
from github_collector.database.database import GitHubDatabase
from github_collector.owners.cleanup import cleanup_orphaned_owners
from github_collector.repository_collector import RepositoryCollector
from github_collector.ui.stats import show_database_stats
from github_collector.utils.logging_config import get_repository_logger
from github_collector.utils.performance_tracker import PerformanceReporter, PerformanceTracker
from github_collector.utils.secrets import SecretManager

# Konfiguriere Logging
logger = get_repository_logger()


def setup_api_client(cache_dir: Optional[str] = None) -> GitHubAPI:
    """
    Richte den GitHub API-Client ein.

    Args:
        cache_dir: Verzeichnis für den API-Cache (optional)

    Returns:
        GitHubAPI-Instanz
    """
    # Hole GitHub-API-Tokens über den SecretManager
    try:
        tokens = SecretManager.get_github_tokens()
    except ValueError as exc:
        # Benutzerfreundliche Fehlermeldung aus SecretManager protokollieren
        # und mit Fehlercode beenden
        logger.error(str(exc))
        sys.exit(1)

    logger.info(f"{len(tokens)} GitHub API-Token(s) gefunden.")

    # Erstelle und gib den API-Client zurück
    return GitHubAPI(tokens, cache_dir=cache_dir)


def parse_arguments() -> argparse.Namespace:
    """
    Parse Kommandozeilenargumente.

    Returns:
        Geparste Argumente
    """
    parser = argparse.ArgumentParser(description="Sammle GitHub-Repositories")

    # Zeitbereichsoptionen
    time_group = parser.add_argument_group("Zeitbereichsoptionen")
    time_group.add_argument(
        "--time-range",
        choices=["week", "month", "year", "custom"],
        help="Vordefinierter Zeitbereich für die Repository-Sammlung",
    )
    time_group.add_argument(
        "--start-date", help="Startdatum für benutzerdefinierten Zeitbereich (YYYY-MM-DD)"
    )
    time_group.add_argument(
        "--end-date", help="Enddatum für benutzerdefinierten Zeitbereich (YYYY-MM-DD)"
    )

    # Sammlungsoptionen
    collection_group = parser.add_argument_group("Sammlungsoptionen")
    collection_group.add_argument(
        "--limit", type=int, help="Maximale Anzahl zu sammelnder Repositories"
    )
    collection_group.add_argument(
        "--all", action="store_true", help="Alle verfügbaren Repositories sammeln"
    )
    collection_group.add_argument(
        "--min-stars",
        type=int,
        default=100,
        help="Minimale Anzahl von Stars für Repositories (Standard: 100)",
    )

    # Datenbankoptionen
    db_group = parser.add_argument_group("Datenbankoptionen")
    db_group.add_argument("--db-path", help="Pfad zur SQLite-Datenbankdatei")

    # Andere Optionen
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Im nicht-interaktiven Modus ausführen (erfordert Zeitbereichs- und Limit-Optionen)",
    )
    parser.add_argument(
        "--stats", action="store_true", help="Datenbankstatistiken anzeigen und beenden"
    )
    parser.add_argument(
        "--cleanup-owners",
        action="store_true",
        help=(
            "Verwaiste Owner-Einträge (Contributors und Organisationen ohne "
            "Repositories) bereinigen"
        ),
    )
    parser.add_argument(
        "--cleanup-dry-run",
        action="store_true",
        help=(
            "Simulationsmodus für die Bereinigung: Zeigt an, welche Einträge "
            "entfernt würden, ohne Änderungen vorzunehmen"
        ),
    )

    # Performance-Tracking-Optionen
    perf_group = parser.add_argument_group("Performance-Tracking-Optionen")
    perf_group.add_argument(
        "--disable-performance-tracking",
        action="store_true",
        help="Deaktiviert das Performance-Tracking während der Ausführung",
    )
    perf_group.add_argument(
        "--performance-output",
        choices=["json", "csv", "log"],
        default="log",
        help="Format für die Ausgabe der Performance-Daten (Standard: log)",
    )
    perf_group.add_argument(
        "--performance-output-path",
        help="Pfad für die Ausgabe der Performance-Daten (bei json oder csv)",
    )
    perf_group.add_argument(
        "--owner-analysis",
        action="store_true",
        help="Führt eine detaillierte Analyse der Owner-Metadaten durch",
    )

    return parser.parse_args()


def interactive_mode(  # noqa: C901
    args: argparse.Namespace, api_client: GitHubAPI, db: GitHubDatabase
) -> None:
    """
    Führe die Repository-Sammlung im interaktiven Modus aus.

    Args:
        args: Kommandozeilenargumente
        api_client: GitHub API-Client
        db: Datenbankverbindung
    """
    # Zeige aktuelle Statistiken an
    show_database_stats(db)

    # Performance-Tracking-Konfiguration
    enable_performance_tracking = (
        not args.disable_performance_tracking and config.PERFORMANCE_CONFIG["enabled"]
    )

    # Initialisiere Repository-Collector mit Performance-Tracking
    collector = RepositoryCollector(
        github_client=api_client, db=db, enable_performance_tracking=enable_performance_tracking
    )

    # Frage nach Zeitbereich, falls nicht angegeben
    if not args.time_range:
        print("\nWähle einen Zeitbereich für die Repository-Sammlung:")
        print("1. Letzte Woche")
        print("2. Letzter Monat")
        print("3. Letztes Jahr")
        print("4. Benutzerdefinierter Zeitbereich")

        choice = input("Deine Wahl (1-4): ").strip()

        if choice == "1":
            args.time_range = "week"
        elif choice == "2":
            args.time_range = "month"
        elif choice == "3":
            args.time_range = "year"
        elif choice == "4":
            args.time_range = "custom"
        else:
            logger.error("Ungültige Auswahl")
            return

    # Berechne Zeitbereich
    now = datetime.now(tz.UTC)

    if args.time_range == "week":
        # Letzte Woche
        end_date = now
        start_date = now - timedelta(days=7)
    elif args.time_range == "month":
        # Letzter Monat
        end_date = now
        start_date = now - timedelta(days=30)
    elif args.time_range == "year":
        # Letztes Jahr
        end_date = now
        start_date = now - timedelta(days=365)
    elif args.time_range == "custom":
        # Benutzerdefinierter Zeitbereich
        if args.start_date and args.end_date:
            try:
                start_date = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=tz.UTC)
                end_date = datetime.strptime(args.end_date, "%Y-%m-%d").replace(tzinfo=tz.UTC)
            except ValueError as e:
                logger.error(f"Ungültiges Datumsformat: {e}")
                return
        else:
            try:
                print("Startdatum (YYYY-MM-DD):")
                start_date_str = input().strip()
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=tz.UTC)

                print("Enddatum (YYYY-MM-DD):")
                end_date_str = input().strip()
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(tzinfo=tz.UTC)
            except ValueError as e:
                logger.error(f"Ungültiges Datumsformat: {e}")
                return
    else:
        logger.error(f"Ungültiger Zeitbereich: {args.time_range}")
        return

    # Frage nach Limit, falls nicht angegeben
    limit = args.limit
    if not limit and not args.all:
        try:
            print("\nMaximale Anzahl zu sammelnder Repositories (leer für unbegrenzt):")
            limit_str = input().strip()
            if limit_str:
                limit = int(limit_str)
        except ValueError:
            logger.error("Ungültige Anzahl")
            return

    # Frage nach minimaler Anzahl von Stars
    try:
        default_stars = args.min_stars
        print(f"\nMinimale Anzahl von Stars (Standard: {default_stars}):")
        min_stars_str = input().strip()
        if min_stars_str:
            min_stars = int(min_stars_str)
        else:
            min_stars = default_stars
    except ValueError:
        logger.error("Ungültige Anzahl von Stars")
        return

    # Frage nach Anzahl der Perioden
    time_diff = end_date - start_date
    days_total = time_diff.days

    # Berechne eine sinnvolle Standardanzahl von Perioden basierend auf der Zeitspanne
    if days_total <= 7:
        default_periods = 1
    elif days_total <= 30:
        default_periods = 3
    elif days_total <= 90:
        default_periods = 6
    elif days_total <= 365:
        default_periods = 12
    else:
        default_periods = days_total // 30  # Etwa eine Periode pro Monat

    periods_count = None
    while periods_count is None:
        try:
            print("\nIn wie viele Perioden soll der Zeitraum unterteilt werden?")
            print(f"(Zeitraum: {days_total} Tage, Vorschlag: {default_periods} Perioden)")
            periods_str = input("Anzahl der Perioden: ").strip()

            if periods_str:
                periods_count = int(periods_str)
                if periods_count <= 0:
                    print("Die Anzahl der Perioden muss größer als 0 sein.")
                    periods_count = None
            else:
                periods_count = default_periods
                print(f"Standard-Wert wird verwendet: {periods_count} Perioden")
        except ValueError:
            print("Bitte geben Sie eine gültige Zahl ein.")

    # Bestätige die Sammlung
    print(
        "\nSammle Repositories von "
        f"{start_date.strftime('%Y-%m-%d')} bis {end_date.strftime('%Y-%m-%d')}"
    )
    print(f"Minimale Anzahl von Stars: {min_stars}")
    print(f"Zeitraum wird in {periods_count} Perioden unterteilt")
    if limit:
        print(f"Maximale Anzahl zu sammelnder Repositories: {limit}")
    else:
        print("Keine Begrenzung der Anzahl zu sammelnder Repositories")

    confirm = input("\nFortfahren? (j/n): ").strip().lower()
    if confirm != "j" and confirm != "ja":
        print("Sammlung abgebrochen")
        return

    # Berechne die Perioden manuell, um die vom Benutzer angegebene Anzahl zu verwenden
    period_size = days_total / periods_count
    periods = []
    current_start = start_date

    for i in range(periods_count):
        # Berechne das Ende dieser Periode
        if i == periods_count - 1:
            # Letzte Periode endet genau am Ende des Zeitraums
            current_end = end_date
        else:
            # Berechne das Ende basierend auf der Periodengröße
            current_end = current_start + timedelta(days=period_size)

        # Füge die Periode hinzu
        periods.append((current_start, current_end))

        # Nächste Periode
        current_start = current_end

    # Sammle Repositories
    try:
        # Setze den Zustand zurück und setze die benutzerdefinierten Perioden
        collector.state.reset(start_date, end_date)
        collector.state.set_time_periods(periods)

        # Starte die Sammlung
        collector.collect_repositories(
            start_date=start_date,
            end_date=end_date,
            min_stars=min_stars,
            max_repos=limit,
            resume=True,
        )

        # Ausgabe der Performance-Daten, wenn Performance-Tracking aktiviert ist
        if enable_performance_tracking:
            performance_reporter = PerformanceReporter(collector.performance_tracker)

            # Ausgabe je nach gewähltem Format
            if args.performance_output == "json":
                output_path = args.performance_output_path or str(
                    config.PERFORMANCE_CONFIG["output_dir"] / "performance_data.json"
                )
                performance_reporter.save_json(output_path)
                logger.info(f"Performance-Daten wurden als JSON in {output_path} gespeichert")
                print(f"\nPerformance-Daten wurden als JSON in {output_path} gespeichert")

            elif args.performance_output == "csv":
                output_path = args.performance_output_path or str(
                    config.PERFORMANCE_CONFIG["output_dir"] / "performance_data"
                )
                csv_files = performance_reporter.save_csv(output_path)
                logger.info(
                    f"Performance-Daten wurden als CSV-Dateien in {output_path} gespeichert"
                )
                print(f"\nPerformance-Daten wurden als CSV-Dateien in {output_path} gespeichert:")
                for file_type, file_path in csv_files.items():
                    print(f"  - {file_type}: {file_path}")

            else:  # log
                print("\nPerformance-Zusammenfassung:")
                performance_reporter.to_log()

    except KeyboardInterrupt:
        print(
            "\nSammlung unterbrochen. Der Fortschritt wurde gespeichert und kann "
            "später fortgesetzt werden."
        )

        # Auch bei Unterbrechung Performance-Daten ausgeben, wenn aktiviert
        if enable_performance_tracking:
            print("\nPerformance-Daten bis zur Unterbrechung:")
            performance_reporter = PerformanceReporter(collector.performance_tracker)
            performance_reporter.to_log()

    except Exception as e:
        logger.error(f"Fehler bei der Repository-Sammlung: {e}")

    # Zeige aktualisierte Statistiken an
    show_database_stats(db)


def non_interactive_mode(  # noqa: C901
    args: argparse.Namespace, api_client: GitHubAPI, db: GitHubDatabase
) -> None:
    """
    Führe die Repository-Sammlung im nicht-interaktiven Modus aus.

    Args:
        args: Kommandozeilenargumente
        api_client: GitHub API-Client
        db: Datenbankverbindung
    """
    # Prüfe, ob alle erforderlichen Argumente angegeben sind
    if not args.time_range and not (args.start_date and args.end_date):
        logger.error(
            "Im nicht-interaktiven Modus muss entweder --time-range oder "
            "--start-date und --end-date angegeben werden"
        )
        return

    # Berechne Zeitbereich
    now = datetime.now(tz.UTC)

    if args.time_range == "week":
        # Letzte Woche
        end_date = now
        start_date = now - timedelta(days=7)
    elif args.time_range == "month":
        # Letzter Monat
        end_date = now
        start_date = now - timedelta(days=30)
    elif args.time_range == "year":
        # Letztes Jahr
        end_date = now
        start_date = now - timedelta(days=365)
    elif args.time_range == "custom" or (args.start_date and args.end_date):
        # Benutzerdefinierter Zeitbereich
        try:
            start_date = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=tz.UTC)
            end_date = datetime.strptime(args.end_date, "%Y-%m-%d").replace(tzinfo=tz.UTC)
        except ValueError as e:
            logger.error(f"Ungültiges Datumsformat: {e}")
            return
    else:
        logger.error(f"Ungültiger Zeitbereich: {args.time_range}")
        return

    # Performance-Tracking-Konfiguration
    enable_performance_tracking = (
        not args.disable_performance_tracking and config.PERFORMANCE_CONFIG["enabled"]
    )

    # Initialisiere Repository-Collector mit Performance-Tracking
    collector = RepositoryCollector(
        github_client=api_client, db=db, enable_performance_tracking=enable_performance_tracking
    )

    # Sammle Repositories
    try:
        collector.collect_repositories(
            start_date=start_date,
            end_date=end_date,
            min_stars=args.min_stars,
            max_repos=args.limit,
            resume=True,
        )

        # Ausgabe der Performance-Daten, wenn Performance-Tracking aktiviert ist
        if enable_performance_tracking:
            performance_reporter = PerformanceReporter(collector.performance_tracker)

            # Ausgabe je nach gewähltem Format
            if args.performance_output == "json":
                output_path = args.performance_output_path or str(
                    config.PERFORMANCE_CONFIG["output_dir"] / "performance_data.json"
                )
                performance_reporter.save_json(output_path)
                logger.info(f"Performance-Daten wurden als JSON in {output_path} gespeichert")

            elif args.performance_output == "csv":
                output_path = args.performance_output_path or str(
                    config.PERFORMANCE_CONFIG["output_dir"] / "performance_data"
                )
                csv_files = performance_reporter.save_csv(output_path)
                logger.info(
                    f"Performance-Daten wurden als CSV-Dateien in {output_path} gespeichert"
                )
                for file_type, file_path in csv_files.items():
                    logger.info(f"  - {file_type}: {file_path}")

            else:  # log
                logger.info("Performance-Zusammenfassung:")
                performance_reporter.to_log()

    except KeyboardInterrupt:
        print(
            "\nSammlung unterbrochen. Der Fortschritt wurde gespeichert und kann "
            "später fortgesetzt werden."
        )

        # Auch bei Unterbrechung Performance-Daten ausgeben, wenn aktiviert
        if enable_performance_tracking:
            logger.info("Performance-Daten bis zur Unterbrechung:")
            performance_reporter = PerformanceReporter(collector.performance_tracker)
            performance_reporter.to_log()

    except Exception as e:
        logger.error(f"Fehler bei der Repository-Sammlung: {e}")


def main() -> int:
    """
    Hauptfunktion für die Repository-Sammlung.

    Returns:
        Exit-Code (0 bei Erfolg, 1 bei Fehler)
    """
    # Parse Kommandozeilenargumente
    args = parse_arguments()

    # Initialisiere Performance-Tracking
    enable_tracking = not args.disable_performance_tracking and config.PERFORMANCE_CONFIG["enabled"]
    performance_tracker = PerformanceTracker(enable_tracking=enable_tracking)

    # Richte API-Client ein
    api_client = setup_api_client()

    # Bestimme Datenbank-URL
    db_url = args.db_path or config.DB_URL

    # Falls ein expliziter Pfad übergeben wurde, der keine SQLAlchemy-URL ist,
    # baue eine SQLite-URL daraus (z.B. "data/github_data.db" -> "sqlite:///data/github_data.db")
    if args.db_path and isinstance(db_url, str) and "://" not in db_url:
        db_url = f"sqlite:///{db_url}"

    # Initialisiere Datenbankverbindung
    db = GitHubDatabase(str(db_url))

    try:
        # Zeige Datenbankstatistiken und beende, falls angefordert
        if args.stats:
            show_database_stats(db)
            return 0

        # Bereinige verwaiste Owner-Einträge, falls angefordert
        if args.cleanup_owners:
            removed_contributors, removed_orgs = cleanup_orphaned_owners(
                db, dry_run=args.cleanup_dry_run
            )
            logger.info(
                "Bereinigung abgeschlossen: %d Contributors und %d " "Organisationen entfernt.",
                removed_contributors,
                removed_orgs,
            )
            return 0

        # Führe im interaktiven oder nicht-interaktiven Modus aus
        if args.non_interactive:
            non_interactive_mode(args, api_client, db)
        else:
            interactive_mode(args, api_client, db)

        # Zeige Performance-Bericht, falls aktiviert
        if performance_tracker.enabled:
            reporter = PerformanceReporter(performance_tracker)

            # Bestimme Ausgabeformat und -pfad
            output_format = args.performance_output
            output_path = args.performance_output_path

            if output_format == "json" and output_path:
                reporter.save_json(output_path)
            elif output_format == "csv" and output_path:
                reporter.save_csv(output_path)
            else:
                reporter.to_log()

        return 0

    except KeyboardInterrupt:
        logger.info("Programm durch Benutzer unterbrochen.")
        return 1

    except Exception as e:
        logger.exception(f"Fehler bei der Ausführung: {e}")
        return 1

    finally:
        # Schließe Datenbankverbindung
        db.close()


if __name__ == "__main__":
    sys.exit(main())
