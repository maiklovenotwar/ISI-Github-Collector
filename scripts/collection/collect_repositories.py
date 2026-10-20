#!/usr/bin/env python3
"""
Skript zum Sammeln von GitHub-Repositories.

Dieses Skript bietet einen interaktiven und nicht-interaktiven Modus zur Sammlung von GitHub-Repositories mit Unterstützung für Star-Range-Filter, Zeitbereiche und weitere Optionen.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

# UTF-8 Output für Windows
sys.stdout.reconfigure(encoding="utf-8")

from dateutil import tz  # noqa: E402

from github_collector import config  # noqa: E402
from github_collector.api.github_api import GitHubAPI  # noqa: E402
from github_collector.database.database import GitHubDatabase  # noqa: E402
from github_collector.monitoring import start_metrics_server  # noqa: E402
from github_collector.repository_collector import RepositoryCollector  # noqa: E402
from github_collector.ui.stats import show_database_stats  # noqa: E402
from github_collector.utils.logging_config import setup_logging  # noqa: E402
from github_collector.utils.secrets import SecretManager  # noqa: E402

logger = setup_logging(log_file=config.LOG_CONFIG["files"]["repository"])


def setup_api_client():
    """Richte den GitHub API-Client ein."""
    # Tokens über den zentralen SecretManager beziehen
    try:
        tokens = SecretManager.get_github_tokens()
    except ValueError as exc:
        logger.error(str(exc))
        sys.exit(1)

    cache_dir = os.environ.get("CACHE_DIR", ".github_cache")
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir)

    return GitHubAPI(tokens, cache_dir=cache_dir)


def report_aborted_collection(collector, error) -> None:
    """Melde einen Abbruch der Sammlung so, dass er nicht zu übersehen ist."""
    period = collector.state.get_current_period()
    where = f"{period[0].isoformat()} bis {period[1].isoformat()}" if period else "unbekannt"
    logger.error(f"Fehler bei der Repository-Sammlung: {error}")
    print(
        "\nSammlung ABGEBROCHEN: "
        f"{error}\n"
        f"Abgebrochen in der Zeitperiode {where}. Diese Periode ist NICHT als erledigt "
        "markiert.\nDer Fortschritt wurde gespeichert; derselbe Aufruf setzt die Sammlung an "
        "dieser Stelle fort.",
        file=sys.stderr,
    )


def parse_arguments():
    """Parse Kommandozeilenargumente."""
    parser = argparse.ArgumentParser(description="Sammle GitHub-Repositories")
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
        "--end-date", help="Enddatum für benutzerdefinierten Zeitbereich (YYYY-MM-DD, inklusiv)"
    )
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
    collection_group.add_argument(
        "--star-range",
        nargs=2,
        type=int,
        metavar=("MIN", "MAX"),
        help="Sammle Repositories mit einer Anzahl an Stars im Bereich MIN bis MAX (inklusive). Ignoriert --min-stars, falls gesetzt.",
    )
    db_group = parser.add_argument_group("Datenbankoptionen")
    db_group.add_argument(
        "--db-path",
        help="Pfad zur Datenbankdatei oder vollständige SQLAlchemy-URL (z.B. mysql+pymysql://user:pw@host/db)",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help=(
            "Nur Datenvolumen analysieren (Total Count & empfohlene Periodenzahl) "
            "und ohne Sammlung beenden"
        ),
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Im nicht-interaktiven Modus ausführen (erfordert Zeitbereichs- und Limit-Optionen)",
    )
    parser.add_argument(
        "--stats", action="store_true", help="Datenbankstatistiken anzeigen und beenden"
    )

    # Monitoring-Optionen (Phase 4.1)
    monitoring_group = parser.add_argument_group("Monitoring-Optionen")
    monitoring_group.add_argument(
        "--enable-metrics",
        action="store_true",
        help="Prometheus-Metriken aktivieren und HTTP-Server starten",
    )
    monitoring_group.add_argument(
        "--metrics-port",
        type=int,
        default=8000,
        help="Port für den Prometheus-Metrics-Server (Standard: 8000)",
    )
    return parser.parse_args()


def interactive_mode(args, api_client, db):  # noqa: C901
    """
    Startet die Repository-Sammlung im interaktiven Modus.

    Fragt den Nutzer nach Zeitbereich und weiteren Parametern, zeigt Statistiken an
    und startet die Sammlung der Repositories über den RepositoryCollector.

    :param args: Kommandozeilenargumente bzw. Namespace
    :param api_client: Initialisierter GitHub API Client
    :param db: Datenbank-Objekt
    """
    # Zeige aktuelle Statistiken an (best effort, ohne explizites Session-Management)
    try:
        show_database_stats(db)
    except Exception as e:
        logger.error(f"Fehler beim Anzeigen der Datenbankstatistiken: {e}")
        print("Konnte keine Datenbankstatistiken anzeigen. Fahre trotzdem fort.")

    # Initialisiere Repository-Collector
    collector = RepositoryCollector(github_client=api_client, db=db)

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
                # end_date inklusiv: auf Ende des Tages setzen (23:59:59)
                end_date = datetime.strptime(args.end_date, "%Y-%m-%d").replace(
                    hour=23, minute=59, second=59, tzinfo=tz.UTC
                )
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
                # end_date inklusiv: auf Ende des Tages setzen (23:59:59)
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(
                    hour=23, minute=59, second=59, tzinfo=tz.UTC
                )
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

    # Frage nach Star-Range oder minimalen Stars
    use_star_range = input("\nMöchtest du einen Star-Bereich verwenden? (j/n): ").strip().lower()
    if use_star_range in ("j", "ja", "y", "yes"):
        try:
            min_stars = int(input("Minimale Anzahl von Stars (inklusive): ").strip())
            max_stars = int(input("Maximale Anzahl von Stars (inklusive): ").strip())
        except ValueError:
            logger.error("Ungültige Eingabe für Star-Bereich.")
            return
        use_range = True
    else:
        try:
            default_stars = args.min_stars if args.min_stars else 10
            print(f"\nMinimale Anzahl von Stars (Standard: {default_stars}):")
            min_stars_str = input().strip()
            if min_stars_str:
                min_stars = int(min_stars_str)
            else:
                min_stars = default_stars
        except ValueError:
            logger.error("Ungültige Anzahl von Stars")
            return
        use_range = False

    # Frage nach Anzahl der Perioden – bevorzugt basierend auf einer
    # Volumenschätzung über die GitHub Search API. Fallback: alte
    # tagebasierte Heuristik, falls die Schätzung fehlschlägt.
    time_diff = end_date - start_date
    days_total = time_diff.days

    try:
        print("\nErmittle Datenvolumen von GitHub (dies kann einen Moment dauern)...")
        estimation = collector.get_data_volume_estimation(
            start_date=start_date,
            end_date=end_date,
            min_stars=min_stars,
        )
        total_count = estimation["total_count"]
        default_periods = estimation["recommended_periods"] or 1

        print(f"Gefundene Repositories im Zeitraum: {total_count}")
        print(
            "Empfohlene Perioden: "
            f"{default_periods} (≈ {estimation['repos_per_period']} Repositories/Periode)"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Konnte Datenvolumen nicht ermitteln (%s). Keine verlässliche Empfehlung verfügbar.",
            e,
        )
        # Keine blinde Heuristik mehr: Nutzer muss die Periodenzahl bewusst wählen.
        # Wir setzen nur einen neutralen Default von 1 Periode.
        default_periods = 1
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
        f"\nSammle Repositories von {start_date.strftime('%Y-%m-%d')} bis {end_date.strftime('%Y-%m-%d')}"
    )
    if use_range:
        print(f"Star-Bereich: {min_stars} bis {max_stars}")
    else:
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
    period_size = days_total / periods_count
    periods = []
    current_start = start_date
    for i in range(periods_count):
        if i == periods_count - 1:
            current_end = end_date
        else:
            current_end = current_start + timedelta(days=period_size)
        periods.append((current_start, current_end))
        current_start = current_end
    collection_failed = False
    try:
        collector.state.reset(start_date, end_date)
        collector.state.set_time_periods(periods)
        if use_range:
            collector.collect_repositories_by_star_range(
                start_date=start_date,
                end_date=end_date,
                min_stars=min_stars,
                max_stars=max_stars,
                max_repos=limit,
                resume=True,
            )
        else:
            collector.collect_repositories(
                start_date=start_date,
                end_date=end_date,
                min_stars=min_stars,
                max_repos=limit,
                resume=True,
            )
    except KeyboardInterrupt:
        print(
            "\nSammlung unterbrochen. Der Fortschritt wurde gespeichert und kann später fortgesetzt werden."
        )
    except Exception as e:
        report_aborted_collection(collector, e)
        collection_failed = True

    # Zeige aktualisierte Statistiken an (best effort)
    try:
        show_database_stats(db)
    except Exception as e:
        logger.error(f"Fehler beim Anzeigen der abschließenden Statistiken: {e}")
        print("Konnte keine abschließenden Statistiken anzeigen.")
        print("Bitte prüfen Sie die Logs für Details.")

    return not collection_failed


def non_interactive_mode(args, api_client, db):
    """
    Führt die Repository-Sammlung im nicht-interaktiven (Batch-)Modus aus.
    Nutzt direkt die übergebenen Parameter und sammelt Repositories automatisiert.
    Zeigt Fortschritt und schreibt Logs.
    :param args: Kommandozeilenargumente bzw. Namespace
    :param api_client: Initialisierter GitHub API Client
    :param db: Datenbank-Objekt
    """
    # Initialisiere Repository-Collector ohne explizites Session-Management
    try:
        collector = RepositoryCollector(github_client=api_client, db=db)
    except Exception as e:
        logger.error(f"Fehler beim Initialisieren im nicht-interaktiven Modus: {e}")
        print(f"Fehler beim Initialisieren: {e}")
        raise

    # Zeitbereich bestimmen
    start_date, end_date = _resolve_time_range(args)

    limit = args.limit if args.limit else None

    # Star-Range-Modus: Wenn --star-range gesetzt ist, wird die neue Methode verwendet
    collection_failed = False
    try:
        if args.star_range:
            min_stars, max_stars = args.star_range
            collector.collect_repositories_by_star_range(
                start_date=start_date,
                end_date=end_date,
                min_stars=min_stars,
                max_stars=max_stars,
                max_repos=limit,
                resume=True,
            )
        else:
            min_stars = args.min_stars if args.min_stars else 0
            collector.collect_repositories(
                start_date=start_date,
                end_date=end_date,
                min_stars=min_stars,
                max_repos=limit,
                resume=True,
            )
    except KeyboardInterrupt:
        print(
            "\nSammlung unterbrochen. Der Fortschritt wurde gespeichert und kann später fortgesetzt werden."
        )
    except Exception as e:
        report_aborted_collection(collector, e)
        collection_failed = True

    return not collection_failed


def _resolve_time_range(args):
    """Bestimme Start- und Enddatum basierend auf CLI-Argumenten.

    Wird im nicht-interaktiven Modus verwendet und kann auch von anderen
    Helfern (z.B. Analyse-Modus) genutzt werden.
    """

    if args.start_date and args.end_date:
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=tz.UTC)
        end_date = datetime.strptime(args.end_date, "%Y-%m-%d").replace(
            hour=23,
            minute=59,
            second=59,
            tzinfo=tz.UTC,
        )
        return start_date, end_date

    now = datetime.now(tz.UTC)
    if args.time_range == "week":
        end_date = now
        start_date = now - timedelta(days=7)
    elif args.time_range == "month":
        end_date = now
        start_date = now - timedelta(days=30)
    elif args.time_range == "year":
        end_date = now
        start_date = now - timedelta(days=365)
    else:
        raise ValueError("Zeitbereich muss angegeben werden")

    return start_date, end_date


def run_analyze_mode(args, api_client, db) -> None:
    """Führt den reinen Analyse-Modus aus (ohne Sammlung).

    Ermittelt das Datenvolumen über die GitHub Search API und gibt eine
    empfohlene Periodenzahl aus.
    """

    collector = RepositoryCollector(github_client=api_client, db=db)

    # Zeitbereich bestimmen (analog zum nicht-interaktiven Modus)
    if args.start_date and args.end_date:
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=tz.UTC)
        end_date = datetime.strptime(args.end_date, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=tz.UTC
        )
    else:
        now = datetime.now(tz.UTC)
        if args.time_range == "week":
            end_date = now
            start_date = now - timedelta(days=7)
        elif args.time_range == "month":
            end_date = now
            start_date = now - timedelta(days=30)
        elif args.time_range == "year":
            end_date = now
            start_date = now - timedelta(days=365)
        else:
            raise ValueError(
                "Für --analyze muss entweder ein Zeitbereich (--time-range) "
                "oder ein explizites Start-/Enddatum angegeben werden."
            )

    # Star-Filter bestimmen (für Analyze verwenden wir die untere Grenze)
    if getattr(args, "star_range", None):
        min_stars = args.star_range[0]
    else:
        min_stars = args.min_stars if args.min_stars is not None else 0

    print("Ermittle Datenvolumen von GitHub...")
    estimation = collector.get_data_volume_estimation(
        start_date=start_date,
        end_date=end_date,
        min_stars=min_stars,
    )

    total = estimation["total_count"]
    periods = estimation["recommended_periods"]
    per_period = estimation["repos_per_period"]

    print("\n=== Datenvolumen-Analyse ===")
    print(
        f"Zeitraum: {start_date.strftime('%Y-%m-%d')} bis "
        f"{end_date.strftime('%Y-%m-%d')} (inkl.)"
    )
    print(f"Gefundene Repositories: {total}")
    print(f"Empfohlene Perioden: {periods}")
    print(f"≈ Repositories pro Periode: {per_period}")


def main():  # noqa: C901
    """Einstiegspunkt für die Repository-Sammlung."""

    args = parse_arguments()

    # Datenbank-URL bestimmen: bevorzugt --db-path, sonst zentrale DB_URL
    db_url = args.db_path or config.DB_URL
    # Falls ein expliziter Pfad übergeben wurde, der keine SQLAlchemy-URL ist, baue eine SQLite-URL daraus
    if args.db_path and isinstance(db_url, str) and "://" not in db_url:
        db_url = f"sqlite:///{os.path.abspath(db_url)}"

    # Initialisiere Datenbank
    db = GitHubDatabase(str(db_url))

    # Zeige nur Statistiken an, falls angefordert
    if args.stats:
        try:
            show_database_stats(db)
        except Exception as e:
            logger.error(f"Fehler beim Anzeigen der Statistiken: {e}")
            print(f"Fehler beim Anzeigen der Statistiken: {e}")
        return

    # Initialisiere API-Client
    try:
        api_client = setup_api_client()
    except ValueError as e:
        logger.error(f"Fehler beim Einrichten des API-Clients: {e}")
        return

    # Prometheus-Metriken aktivieren (Phase 4.1)
    if getattr(args, "enable_metrics", False):
        metrics_port = getattr(args, "metrics_port", 8000)
        if start_metrics_server(metrics_port):
            print(f"📊 Prometheus-Metriken verfügbar unter http://localhost:{metrics_port}/metrics")
        else:
            logger.warning("Metrics-Server konnte nicht gestartet werden")

    # Analyse-Modus: Nur Datenvolumen bestimmen und dann beenden
    if getattr(args, "analyze", False):
        try:
            run_analyze_mode(args, api_client, db)
        except Exception as e:  # noqa: BLE001
            logger.error("Fehler bei der Datenvolumen-Analyse: %s", e)
            print(f"Fehler bei der Datenvolumen-Analyse: {e}")
        return

    # Führe im interaktiven oder nicht-interaktiven Modus aus
    if args.non_interactive:
        completed = non_interactive_mode(args, api_client, db)
    else:
        # Im interaktiven Modus: Falls --star-range gesetzt ist, Hinweis anzeigen
        if hasattr(args, "star_range") and args.star_range:
            print(
                "Hinweis: Die Star-Range-Option (--star-range) ist nur im nicht-interaktiven Modus direkt verfügbar. Für interaktive Nutzung bitte den nicht-interaktiven Modus verwenden."
            )
        completed = interactive_mode(args, api_client, db)

    # Ein Abbruch der Sammlung darf nicht wie ein erfolgreicher Lauf enden
    return 1 if completed is False else 0


if __name__ == "__main__":
    sys.exit(main())
