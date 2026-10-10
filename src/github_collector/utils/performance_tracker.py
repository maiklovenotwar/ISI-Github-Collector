"""Performance-Tracking für GitHub Data Collector."""

import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from functools import wraps
from time import perf_counter
from typing import Any, Dict, Optional

from github_collector.config import PERFORMANCE_CONFIG

logger = logging.getLogger(__name__)


@dataclass
class ApiMetrics:
    """Metriken für API-Aufrufe."""

    total_calls: int = 0
    total_time: float = 0.0
    min_time: float = float("inf")
    max_time: float = 0.0

    @property
    def avg_time(self) -> float:
        """Durchschnittliche Antwortzeit."""
        if self.total_calls == 0:
            return 0.0
        return self.total_time / self.total_calls

    def add_call(self, duration: float) -> None:
        """Fügt einen API-Aufruf hinzu."""
        self.total_calls += 1
        self.total_time += duration
        self.min_time = min(self.min_time, duration)
        self.max_time = max(self.max_time, duration)


@dataclass
class TokenUsage:
    """Nutzungsstatistiken für ein API-Token."""

    token: str
    calls: int = 0
    last_used: float = 0.0

    def record_usage(self) -> None:
        """Zeichnet eine Tokennutzung auf."""
        self.calls += 1
        self.last_used = time.time()


class PerformanceTracker:
    """
    Tracker für Performance-Metriken.

    Diese Klasse sammelt und analysiert Performance-Metriken für verschiedene
    Operationen im GitHub Data Collector.
    """

    _instance = None

    def __new__(cls, *args, **kwargs):
        """Implementiert das Singleton-Pattern.

        Akzeptiert beliebige Argumente, die an __init__ weitergegeben werden.
        """
        if cls._instance is None:
            cls._instance = super(PerformanceTracker, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, enable_tracking: bool = None, verbose: bool = True):
        """Initialisiert den Performance-Tracker.

        Args:
            enable_tracking: Aktiviert oder deaktiviert das Performance-Tracking.
                Wenn None, wird die Umgebungsvariable ENABLE_PERFORMANCE_TRACKING
                verwendet (siehe config.PERFORMANCE_CONFIG).
                Wenn diese nicht gesetzt ist, ist das Tracking standardmäßig aktiviert.
            verbose: Aktiviert oder deaktiviert ausführliche Ausgaben während der
                Ausführung.
        """
        if self._initialized:
            # Wenn bereits initialisiert, aktualisiere nur die Flags
            if enable_tracking is not None:
                self.enabled = enable_tracking
            if verbose is not None:
                self.verbose = verbose
            return

        # Verwende zentrale Konfiguration, wenn enable_tracking nicht explizit gesetzt wurde
        if enable_tracking is None:
            enable_tracking = PERFORMANCE_CONFIG["enabled"]

        # Allgemeine Performance-Metriken
        self.start_time = perf_counter()
        self.repositories_processed = 0
        self.total_processing_time = 0.0
        self.batch_times = []

        # API-spezifische Metriken
        self.api_metrics = defaultdict(ApiMetrics)
        self.cache_hits = defaultdict(int)  # Zählt Cache-Treffer pro Endpunkt

        # Token-Nutzung
        self.token_usage = {}
        self.token_rotations = []

        # Owner-Statistiken
        self.new_owners = {"contributors": 0, "organizations": 0}
        self.duplicate_owners_in_batch = 0
        self.known_owners_requested = 0

        # Repository-Statistiken
        self.duplicate_repositories = 0

        # Flags
        self.enabled = enable_tracking
        self.verbose = verbose
        self._initialized = True

        if self.enabled:
            logger.info(f"Performance-Tracking aktiviert (verbose={self.verbose})")
        else:
            logger.info("Performance-Tracking deaktiviert")

    def reset(self) -> None:
        """Setzt alle Metriken zurück."""
        self.__init__()

    def start_repository_processing(self) -> float:
        """
        Startet die Zeitmessung für die Verarbeitung eines Repositories.

        Returns:
            Startzeitstempel
        """
        return perf_counter()

    def end_repository_processing(self, start_time: float, repo_name: str) -> float:
        """
        Beendet die Zeitmessung für die Verarbeitung eines Repositories.

        Args:
            start_time: Startzeitstempel
            repo_name: Name des Repositories

        Returns:
            Verarbeitungsdauer in Sekunden
        """
        if not self.enabled:
            return 0.0

        end_time = perf_counter()
        processing_time = end_time - start_time

        self.repositories_processed += 1
        self.total_processing_time += processing_time

        if self.verbose:
            print(f"\r⏱ Repository {repo_name} verarbeitet in {processing_time:.2f} Sekunden")

            # Zeige Durchschnitt an, wenn mehrere Repositories verarbeitet wurden
            if self.repositories_processed > 1:
                avg_time = self.total_processing_time / self.repositories_processed
                print(
                    "   Durchschnitt: "
                    f"{avg_time:.2f} Sekunden pro Repository "
                    f"({self.repositories_processed} gesamt)"
                )

        return processing_time

    def start_batch_processing(self) -> float:
        """
        Startet die Zeitmessung für die Verarbeitung eines Batches.

        Returns:
            Startzeitstempel
        """
        return perf_counter()

    def end_batch_processing(self, start_time: float, batch_size: int) -> float:
        """
        Beendet die Zeitmessung für die Verarbeitung eines Batches.

        Args:
            start_time: Startzeitstempel
            batch_size: Anzahl der Repositories im Batch

        Returns:
            Verarbeitungsdauer in Sekunden
        """
        if not self.enabled:
            return 0.0

        end_time = perf_counter()
        processing_time = end_time - start_time

        self.batch_times.append((batch_size, processing_time))

        if self.verbose:
            print(
                "\n⏳ Batch mit "
                f"{batch_size} Repositories verarbeitet in {processing_time:.2f} Sekunden"
            )
            if batch_size > 0:
                print(
                    "   Durchschnitt im Batch: "
                    f"{processing_time / batch_size:.2f} Sekunden pro Repository"
                )

        return processing_time

    def record_api_call(self, endpoint: str, duration: float) -> None:
        """
        Zeichnet einen API-Aufruf auf.

        Args:
            endpoint: API-Endpunkt (z.B. 'get_user', 'get_repository')
            duration: Dauer des Aufrufs in Sekunden
        """
        if not self.enabled:
            return

        self.api_metrics[endpoint].add_call(duration)

    def record_cache_hit(self, endpoint: str) -> None:
        """
        Zeichnet einen Cache-Treffer auf.

        Args:
            endpoint: API-Endpunkt, für den ein Cache-Treffer erfolgte
        """
        if not self.enabled:
            return

        self.cache_hits[endpoint] += 1

    def record_token_usage(self, token: str) -> None:
        """
        Zeichnet die Nutzung eines Tokens auf.

        Args:
            token: API-Token (gekürzt für die Sicherheit)
        """
        if not self.enabled:
            return

        # Verwende nur die ersten 5 und letzten 5 Zeichen des Tokens für die Protokollierung
        safe_token = f"{token[:5]}...{token[-5:]}" if len(token) > 10 else "***"

        if safe_token not in self.token_usage:
            self.token_usage[safe_token] = TokenUsage(token=safe_token)

        self.token_usage[safe_token].record_usage()

    def record_token_rotation(self, from_token: str, to_token: str, reason: str) -> None:
        """
        Zeichnet eine Token-Rotation auf.

        Args:
            from_token: Vorheriges Token (gekürzt)
            to_token: Neues Token (gekürzt)
            reason: Grund für die Rotation
        """
        if not self.enabled:
            return

        # Verwende nur die ersten 5 und letzten 5 Zeichen des Tokens für die Protokollierung
        safe_from = f"{from_token[:5]}...{from_token[-5:]}" if len(from_token) > 10 else "***"
        safe_to = f"{to_token[:5]}...{to_token[-5:]}" if len(to_token) > 10 else "***"

        self.token_rotations.append(
            {
                "timestamp": time.time(),
                "from_token": safe_from,
                "to_token": safe_to,
                "reason": reason,
            }
        )

    def record_new_owner(self, owner_type: str) -> None:
        """
        Zeichnet einen neuen Owner (Contributor oder Organization) auf.

        Args:
            owner_type: Typ des Owners ('contributors' oder 'organizations')
        """
        if not self.enabled or owner_type not in ("contributors", "organizations"):
            return

        self.new_owners[owner_type] += 1

    def record_duplicate_owner_in_batch(self) -> None:
        """
        Zeichnet auf, dass ein Owner mehrfach im selben Batch vorkommt.
        """
        if not self.enabled:
            return

        self.duplicate_owners_in_batch += 1

    def record_known_owner_requested(self) -> None:
        """
        Zeichnet auf, dass ein bereits bekannter Owner erneut angefragt wurde.
        """
        if not self.enabled:
            return

        self.known_owners_requested += 1

    def record_duplicate_repository(self) -> None:
        """
        Zeichnet ein doppeltes Repository auf, das bereits in der Datenbank existiert.
        """
        if not self.enabled:
            return

        self.duplicate_repositories += 1

    def get_summary(self) -> Dict[str, Any]:
        """
        Gibt eine Zusammenfassung der Performance-Metriken zurück.

        Returns:
            Dictionary mit Performance-Metriken
        """
        if not self.enabled:
            return {"enabled": False}

        end_time = perf_counter()
        total_time = end_time - self.start_time

        # Berechne Batch-Statistiken
        batch_stats = {
            "count": len(self.batch_times),
            "total_repos": sum(size for size, _ in self.batch_times),
            "total_time": sum(time for _, time in self.batch_times),
            "avg_size": (
                sum(size for size, _ in self.batch_times) / len(self.batch_times)
                if self.batch_times
                else 0
            ),
            "avg_time": (
                sum(time for _, time in self.batch_times) / len(self.batch_times)
                if self.batch_times
                else 0
            ),
        }

        # Berechne API-Statistiken
        api_stats = {}
        for endpoint, metrics in self.api_metrics.items():
            api_stats[endpoint] = {
                "calls": metrics.total_calls,
                "total_time": metrics.total_time,
                "avg_time": metrics.avg_time,
                "min_time": metrics.min_time if metrics.min_time != float("inf") else 0,
                "max_time": metrics.max_time,
                "cache_hits": self.cache_hits.get(endpoint, 0),
            }

        # Berechne Token-Statistiken
        token_stats = {}
        for token, usage in self.token_usage.items():
            token_stats[token] = {"calls": usage.calls, "last_used": usage.last_used}

        # Owner-Statistiken
        owner_stats = {
            "new_contributors": self.new_owners["contributors"],
            "new_organizations": self.new_owners["organizations"],
            "duplicate_in_batch": self.duplicate_owners_in_batch,
            "known_requested": self.known_owners_requested,
        }

        # Repository-Statistiken
        repo_stats = {
            "processed": self.repositories_processed,
            "processing_time": self.total_processing_time,
            "avg_time": (
                self.total_processing_time / self.repositories_processed
                if self.repositories_processed > 0
                else 0
            ),
            "duplicate_repositories": self.duplicate_repositories,
        }

        return {
            "enabled": True,
            "timestamp": time.time(),
            "total_time": total_time,
            "repositories": repo_stats,
            "batches": batch_stats,
            "api": api_stats,
            "cache": {
                "total_hits": sum(self.cache_hits.values()),
                "hits_by_endpoint": dict(self.cache_hits),
            },
            "tokens": {"usage": token_stats, "rotations": self.token_rotations},
            "owners": owner_stats,
            "overhead": total_time - self.total_processing_time,
        }

    def print_summary(self) -> None:
        """Gibt eine Zusammenfassung der Performance-Metriken aus."""
        if not self.enabled:
            return

        summary = self.get_summary()

        print("\n" + "=" * 80)
        print("PERFORMANCE-ZUSAMMENFASSUNG")
        print("=" * 80)

        # Allgemeine Statistiken
        print(
            "\n⏰ Gesamtzeit: "
            f"{summary['total_time']:.2f} Sekunden für "
            f"{summary['repositories']['processed']} Repositories"
        )

        if summary["repositories"]["processed"] > 0:
            print(
                "   Durchschnitt gesamt: "
                f"{summary['repositories']['avg_time']:.2f} Sekunden pro Repository"
            )

        print(
            "   Reine Verarbeitungszeit: "
            f"{summary['repositories']['processing_time']:.2f} Sekunden"
        )
        print(f"   Overhead (API, Suche, etc.): {summary['overhead']:.2f} Sekunden")

        # API-Statistiken
        if summary["api"]:
            print("\nAPI-AUFRUFE:")
            print("-" * 80)
            print(
                f"{'Endpunkt':<30} {'Aufrufe':<10} {'Gesamt (s)':<15} "
                f"{'Durchschnitt (s)':<15} {'Min (s)':<10} {'Max (s)':<10}"
            )
            print("-" * 80)
            for endpoint, stats in sorted(
                summary["api"].items(), key=lambda x: x[1]["calls"], reverse=True
            ):
                print(
                    f"{endpoint:<30} {stats['calls']:<10} "
                    f"{stats['total_time']:<15.2f} {stats['avg_time']:<15.2f} "
                    f"{stats['min_time']:<10.2f} {stats['max_time']:<10.2f}"
                )

        # Token-Statistiken
        if summary["tokens"]["usage"]:
            print("\nTOKEN-NUTZUNG:")
            print("-" * 80)
            print(f"{'Token':<20} {'Aufrufe':<10} {'Zuletzt verwendet':<30}")
            print("-" * 80)
            print(f"{'Token':<20} {'Aufrufe':<10} {'Zuletzt verwendet':<30}")
            print("-" * 80)

            for token, stats in sorted(
                summary["tokens"]["usage"].items(), key=lambda x: x[1]["calls"], reverse=True
            ):
                last_used = (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stats["last_used"]))
                    if stats["last_used"] > 0
                    else "Nie"
                )
                print(f"{token:<20} {stats['calls']:<10} {last_used:<30}")

        # Token-Rotationen
        if summary["tokens"]["rotations"]:
            print("\nTOKEN-ROTATIONEN:")
            print("-" * 80)
            print(f"{'Zeitpunkt':<20} {'Von':<20} {'Zu':<20} {'Grund':<40}")
            print("-" * 80)

            for rotation in summary["tokens"]["rotations"]:
                timestamp = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(rotation["timestamp"])
                )
                print(
                    f"{timestamp:<20} {rotation['from_token']:<20} "
                    f"{rotation['to_token']:<20} {rotation['reason']:<40}"
                )

        print("\n" + "=" * 80)


class PerformanceReporter:
    """
    Klasse zur Aggregation und Ausgabe von Performance-Daten.

    Diese Klasse ist unabhängig vom Logger und kann Performance-Daten
    in verschiedenen Formaten ausgeben.
    """

    def __init__(self, tracker: PerformanceTracker):
        """
        Initialisiert den Performance-Reporter.

        Args:
            tracker: Performance-Tracker, dessen Daten ausgegeben werden sollen
        """
        self.tracker = tracker

    def to_json(self, file_path: Optional[str] = None) -> Optional[str]:
        """
        Gibt die Performance-Daten als JSON aus.

        Args:
            file_path: Pfad zur Ausgabedatei. Wenn None, wird der JSON-String zurückgegeben.

        Returns:
            JSON-String, wenn file_path None ist, sonst None
        """
        summary = self.tracker.get_summary()
        json_data = json.dumps(summary, indent=2)

        if file_path:
            with open(file_path, "w") as f:
                f.write(json_data)
            logger.info(f"Performance-Daten wurden in {file_path} gespeichert")
            return None

        return json_data

    def to_log(self, level: int = logging.INFO) -> None:
        """
        Gibt die Performance-Daten als strukturierte Logs aus.

        Args:
            level: Log-Level (Standard: INFO)
        """
        summary = self.tracker.get_summary()
        if not summary.get("enabled", False):
            logger.info("Performance-Tracking ist deaktiviert, keine Daten verfügbar")
            return

        logger.log(
            level,
            "Performance-Zusammenfassung: "
            f"{summary['total_time']:.2f} Sekunden für "
            f"{summary['repositories']['processed']} Repositories",
        )

        # API-Statistiken
        for endpoint, stats in sorted(
            summary["api"].items(), key=lambda x: x[1]["calls"], reverse=True
        ):
            logger.log(
                level,
                "API %s: %d Aufrufe, %.2fs gesamt, %.4fs durchschnittlich, %d Cache-Treffer",
                endpoint,
                stats["calls"],
                stats["total_time"],
                stats["avg_time"],
                stats["cache_hits"],
            )

        # Owner-Statistiken
        owners = summary["owners"]
        logger.log(
            level,
            "Owners: %d neue Contributors, %d neue Organizations",
            owners["new_contributors"],
            owners["new_organizations"],
        )
        logger.log(
            level,
            "Owner-Anomalien: %d Duplikate im Batch, %d bekannte Owner erneut angefragt",
            owners["duplicate_in_batch"],
            owners["known_requested"],
        )

    def to_csv(self, base_path: Optional[str] = None) -> Dict[str, str]:
        """
        Gibt die Performance-Daten als CSV-Dateien aus.

        Args:
            base_path: Basis-Pfad für die CSV-Dateien

        Returns:
            Dictionary mit den Pfaden zu den erstellten CSV-Dateien
        """
        import csv
        from datetime import datetime

        summary = self.tracker.get_summary()
        if not summary.get("enabled", False):
            logger.info("Performance-Tracking ist deaktiviert, keine Daten verfügbar")
            return {}

        if base_path is None:
            base_path = str(PERFORMANCE_CONFIG["output_dir"])

        timestamp = datetime.fromtimestamp(summary["timestamp"]).strftime("%Y%m%d_%H%M%S")
        os.makedirs(base_path, exist_ok=True)

        created_files = {}

        # API-Statistiken
        api_file = os.path.join(base_path, f"api_stats_{timestamp}.csv")
        with open(api_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "endpoint",
                    "calls",
                    "total_time",
                    "avg_time",
                    "min_time",
                    "max_time",
                    "cache_hits",
                ]
            )
            for endpoint, stats in summary["api"].items():
                writer.writerow(
                    [
                        endpoint,
                        stats["calls"],
                        stats["total_time"],
                        stats["avg_time"],
                        stats["min_time"],
                        stats["max_time"],
                        stats["cache_hits"],
                    ]
                )
        created_files["api"] = api_file

        # Owner-Statistiken
        owner_file = os.path.join(base_path, f"owner_stats_{timestamp}.csv")
        with open(owner_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["metric", "value"])
            for key, value in summary["owners"].items():
                writer.writerow([key, value])
        created_files["owners"] = owner_file

        # Allgemeine Statistiken
        general_file = os.path.join(base_path, f"general_stats_{timestamp}.csv")
        with open(general_file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["metric", "value"])
            writer.writerow(["total_time", summary["total_time"]])
            writer.writerow(["repositories_processed", summary["repositories"]["processed"]])
            writer.writerow(["processing_time", summary["repositories"]["processing_time"]])
            writer.writerow(["avg_time_per_repo", summary["repositories"]["avg_time"]])
            writer.writerow(["overhead", summary["overhead"]])
            writer.writerow(["total_cache_hits", summary["cache"]["total_hits"]])
        created_files["general"] = general_file

        logger.info(f"Performance-Daten wurden als CSV-Dateien in {base_path} gespeichert")
        return created_files


def api_call(endpoint: str):
    """
    Dekorator für API-Aufrufe, der die Performance misst.

    Args:
        endpoint: Name des API-Endpunkts
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            tracker = PerformanceTracker()

            # Wenn Tracking deaktiviert ist, führe die Funktion direkt aus
            if not tracker.enabled:
                return func(*args, **kwargs)

            # Starte Zeitmessung
            start_time = perf_counter()

            try:
                # Führe die API-Methode aus
                result = func(*args, **kwargs)
                return result
            finally:
                # Ende der Zeitmessung
                end_time = perf_counter()
                duration = end_time - start_time

                # Zeichne den API-Aufruf auf
                tracker.record_api_call(endpoint, duration)

        return wrapper

    return decorator
