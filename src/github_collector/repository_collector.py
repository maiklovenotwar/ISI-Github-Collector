"""Repository Collector für die effiziente Sammlung von GitHub-Repositories."""

import json
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from math import ceil
from time import perf_counter
from typing import Any, Dict, List, Optional, Tuple

from dateutil import tz
from pydantic import ValidationError

from github_collector.config import (
    BATCH_CONFIG,
    ENABLE_PARALLEL_PROCESSING,
    GITHUB_API,
    MAX_WORKER_THREADS,
    STATE_CONFIG,
)
from github_collector.monitoring import (
    batch_processing_seconds,
    record_repository_processed,
    update_active_threads,
    update_batch_size,
)
from github_collector.ui.progress import (
    show_api_limit_reached,
    show_collection_complete,
    show_collection_progress,
    show_collection_summary,
    show_max_repos_reached,
    show_period_progress,
    show_period_too_large,
    show_repositories_found,
)
from github_collector.utils.performance import AdaptiveBatchConfig, AdaptiveBatchManager
from github_collector.utils.performance_tracker import PerformanceTracker

from .api.github_api import (
    GitHubAPI,
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubRateLimitError,
)
from .database.database import GitHubDatabase, session_scope
from .owners.owner_processor import OwnerProcessor
from .schemas import RepositorySchema

logger = logging.getLogger(__name__)


class CollectionState:
    """Verwaltet den Zustand der Repository-Sammlung.

    Diese Klasse speichert und lädt den Zustand der Sammlung, um eine
    unterbrechbare und fortsetzbare Sammlung zu ermöglichen.
    """

    def __init__(self, state_file: str = None):
        """
        Initialisiere den Sammlungszustand.

        Args:
            state_file: Pfad zur Zustandsdatei
        """
        self.state_file = state_file or str(STATE_CONFIG["file"])
        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        """
        Lade den Zustand aus der Datei.

        Returns:
            Zustandsdaten oder leeres Dictionary, wenn keine Datei existiert
        """
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Fehler beim Laden des Sammlungszustands: {e}")

        # Standardzustand, wenn keine Datei existiert oder ein Fehler auftritt
        return {
            "last_run": None,
            "time_periods": [],
            "current_period_index": 0,
            "current_period_page": 1,
            "repositories_collected": 0,
            "start_date": None,
            "end_date": None,
        }

    def save(self) -> None:
        """
        Speichere den aktuellen Zustand in der Datei.
        """
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2, default=str)
            logger.debug(f"Sammlungszustand gespeichert in {self.state_file}")
        except Exception as e:
            logger.error(f"Fehler beim Speichern des Sammlungszustands: {e}")

    def update(self, **kwargs) -> None:
        """
        Aktualisiere den Zustand mit den angegebenen Werten.

        Args:
            **kwargs: Schlüssel-Wert-Paare für die Aktualisierung
        """
        self.state.update(kwargs)
        self.save()

    def reset(
        self, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None
    ) -> None:
        """
        Setze den Zustand zurück für eine neue Sammlung.

        Args:
            start_date: Startdatum für die Sammlung
            end_date: Enddatum für die Sammlung
        """
        self.state = {
            "last_run": datetime.now().isoformat(),
            "time_periods": [],
            "current_period_index": 0,
            "current_period_page": 1,
            "repositories_collected": 0,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        }
        self.save()

    def get(self, key: str, default: Any = None) -> Any:
        """
        Rufe einen Wert aus dem Zustand ab.

        Args:
            key: Schlüssel
            default: Standardwert, falls der Schlüssel nicht existiert

        Returns:
            Wert oder Standardwert
        """
        return self.state.get(key, default)

    def set_time_periods(self, periods: List[Tuple[datetime, datetime]]) -> None:
        """
        Setze die Zeitperioden für die Sammlung.

        Args:
            periods: Liste von (Start, Ende) Tupeln
        """
        self.state["time_periods"] = [
            {"start": start.isoformat(), "end": end.isoformat()} for start, end in periods
        ]
        self.save()

    def get_current_period(self) -> Optional[Tuple[datetime, datetime]]:
        """
        Rufe die aktuelle Zeitperiode ab.

        Returns:
            Tupel (Start, Ende) oder None, wenn keine Perioden definiert sind
        """
        periods = self.state.get("time_periods", [])
        index = self.state.get("current_period_index", 0)

        if not periods or index >= len(periods):
            return None

        period = periods[index]
        start = datetime.fromisoformat(period["start"])
        end = datetime.fromisoformat(period["end"])

        return (start, end)

    def next_period(self) -> bool:
        """
        Wechsle zur nächsten Zeitperiode.

        Returns:
            True, wenn eine weitere Periode verfügbar ist, sonst False
        """
        index = self.state.get("current_period_index", 0)
        periods = self.state.get("time_periods", [])

        if index + 1 < len(periods):
            self.state["current_period_index"] = index + 1
            self.state["current_period_page"] = 1
            self.save()
            return True

        return False


class RepositoryCollector:
    """
    Sammelt GitHub-Repositories mit einer zeitbasierten Strategie.

    Diese Klasse implementiert eine effiziente Strategie zur Sammlung von
    GitHub-Repositories, indem sie die Sammlung in kleine Zeitperioden aufteilt,
    um die API-Limits zu umgehen und eine vollständige Sammlung zu ermöglichen.
    """

    def __init__(
        self,
        github_client: GitHubAPI,
        db: GitHubDatabase,
        state_file: str = None,
        enable_performance_tracking: bool = None,
    ):
        """
        Initialisiere den Repository Collector.

        Args:
            github_client: GitHub API Client
            db: Datenbankverbindung
            state_file: Pfad zur Zustandsdatei
            enable_performance_tracking: Aktiviert oder deaktiviert das Performance-Tracking
        """
        # Performance-Tracker initialisieren
        self.performance_tracker = PerformanceTracker(enable_tracking=enable_performance_tracking)

        # API-Client mit Performance-Tracker initialisieren
        self.github = github_client
        self.api = github_client  # Alias für die neuen Funktionen

        # Setze den Performance-Tracker im API-Client
        if hasattr(self.api, "performance_tracker"):
            self.api.performance_tracker = self.performance_tracker

        # Datenbankverbindung und Zustand
        self.db = db
        self.state = CollectionState(state_file or str(STATE_CONFIG["file"]))

        # Owner-Processor initialisieren
        self.owner_processor = OwnerProcessor(self.api, self.db, self.performance_tracker)

        # Dynamischer Batch-Manager (Phase 2.2)
        self.batch_manager = AdaptiveBatchManager(AdaptiveBatchConfig())

        # Logge Parallelisierungs-Konfiguration und aktuelle Batch-Größe beim Start
        logger.info(
            "PARALLEL_CONFIG: Enabled=%s, Workers=%s, BatchSize=%s",
            ENABLE_PARALLEL_PROCESSING,
            MAX_WORKER_THREADS,
            self.batch_manager.current_size,
        )

        # Gesamte Verarbeitungszeit über alle Batches (nur Repository-Verarbeitung)
        self._total_processing_time: float = 0.0

    def _calculate_time_periods(
        self, start_date: datetime, end_date: datetime, initial_period_days: int = None
    ) -> List[Tuple[datetime, datetime]]:
        """
        Berechne optimale Zeitperioden für die Sammlung.

        Diese Methode teilt den Zeitraum in kleinere Perioden auf, wobei die Größe
        der Perioden dynamisch angepasst wird, um die API-Limits zu berücksichtigen.

        Args:
            start_date: Startdatum für die Sammlung
            end_date: Enddatum für die Sammlung
            initial_period_days: Anfängliche Periodengröße in Tagen

        Returns:
            Liste von (Start, Ende) Tupeln für die Zeitperioden
        """
        periods = []
        current_start = start_date
        if initial_period_days is None or initial_period_days <= 0:
            period_days = BATCH_CONFIG["repository_initial_period_days"]
        else:
            period_days = initial_period_days

        while current_start < end_date:
            # Berechne das Ende dieser Periode
            current_end = min(current_start + timedelta(days=period_days), end_date)

            # Füge die Periode hinzu
            periods.append((current_start, current_end))

            # Nächste Periode
            current_start = current_end

        return periods

    def _adjust_period_size(
        self, period: Tuple[datetime, datetime], results_count: int, max_results: int = 1000
    ) -> List[Tuple[datetime, datetime]]:
        """
        Passe die Größe einer Zeitperiode an, wenn zu viele Ergebnisse gefunden wurden.

        Args:
            period: Zeitperiode als (Start, Ende) Tupel
            results_count: Anzahl der gefundenen Repositories
            max_results: Maximale Anzahl von Ergebnissen pro Periode

        Returns:
            Liste von angepassten Zeitperioden
        """
        start, end = period

        if max_results <= 0:
            max_results = GITHUB_API["search_max_results"]

        # Wenn die Anzahl der Ergebnisse akzeptabel ist, behalte die Periode bei
        if results_count <= max_results:
            return [period]

        # Berechne, wie viele Unterperioden wir benötigen
        num_subperiods = (results_count // max_results) + 1

        # Berechne die Dauer jeder Unterperiode
        total_seconds = (end - start).total_seconds()
        subperiod_seconds = total_seconds / num_subperiods

        # Erstelle die Unterperioden
        subperiods = []
        subperiod_start = start

        for i in range(num_subperiods):
            subperiod_end = subperiod_start + timedelta(seconds=subperiod_seconds)

            # Stelle sicher, dass die letzte Unterperiode genau bis zum Ende der Originalperiode geht
            if i == num_subperiods - 1:
                subperiod_end = end

            subperiods.append((subperiod_start, subperiod_end))
            subperiod_start = subperiod_end

        return subperiods

    def _search_repositories_in_period(
        self,
        start_date: datetime,
        end_date: datetime,
        min_stars: int = 0,
        page: int = 1,
        max_stars: int = None,
    ) -> dict:
        """
        Suche nach Repositories in einer bestimmten Zeitperiode.

        Args:
            start_date: Startdatum
            end_date: Enddatum
            min_stars: Minimale Anzahl von Stars
            max_stars: Maximale Anzahl von Stars (optional)
            page: Seitennummer

        Returns:
            Suchergebnisse als Dictionary

        Raises:
            GitHubAPIError: Wenn die Suche fehlschlägt. Ein Fehler darf nicht wie eine
                leere Periode aussehen - sonst würde die Periode als erledigt markiert
                und nie nachgeholt.
        """
        # Erstelle die Suchanfrage
        if max_stars is not None:
            query = f"created:{start_date.isoformat()}..{end_date.isoformat()} stars:{min_stars}..{max_stars}"
        else:
            query = f"created:{start_date.isoformat()}..{end_date.isoformat()} stars:>={min_stars}"

        # Führe die Suche durch (per_page fest aus Konfiguration, z.B. 100)
        return self.github.search_repositories(
            query=query,
            sort="stars",
            order="desc",
            per_page=GITHUB_API["search_page_size"],
            page=page,
        )

    def _initialize_or_resume_star_range_collection(
        self,
        start_date: datetime,
        end_date: datetime,
        min_stars: int,
        max_stars: int,
        resume: bool,
    ) -> None:
        """Initialisiere neue oder setze unterbrochene Star-Range-Sammlung fort.

        Args:
            start_date: Startdatum
            end_date: Enddatum
            min_stars: Minimale Anzahl von Stars
            max_stars: Maximale Anzahl von Stars
            resume: Ob unterbrochene Sammlung fortgesetzt werden soll
        """
        if resume and self.state.state.get("time_periods"):
            # Fortsetzen einer unterbrochenen Sammlung
            state_start_date = datetime.fromisoformat(self.state.get("start_date"))
            state_end_date = datetime.fromisoformat(self.state.get("end_date"))

            total_repos = self._get_total_repositories_in_timeframe(
                start_date=state_start_date,
                end_date=state_end_date,
                min_stars=min_stars,
                max_stars=max_stars,
            )
            if total_repos > 0:
                print(
                    f"\nInsgesamt gefundene Repositories im Zeitraum {state_start_date.strftime('%Y-%m-%d')} bis {state_end_date.strftime('%Y-%m-%d')} mit Stars {min_stars}..{max_stars}: {total_repos}"
                )
        else:
            # Neue Sammlung starten
            logger.info(
                f"Starte neue Sammlung von {start_date} bis {end_date} für Stars {min_stars}..{max_stars}"
            )
            total_repos = self._get_total_repositories_in_timeframe(
                start_date=start_date, end_date=end_date, min_stars=min_stars, max_stars=max_stars
            )
            if total_repos > 0:
                print(
                    f"\nInsgesamt gefundene Repositories im Zeitraum {start_date.strftime('%Y-%m-%d')} bis {end_date.strftime('%Y-%m-%d')} mit Stars {min_stars}..{max_stars}: {total_repos}"
                )

            # Berechne Zeitperioden und initialisiere State
            periods = self._calculate_time_periods(start_date, end_date)
            self.state.reset(start_date, end_date)
            self.state.set_time_periods(periods)

    def collect_repositories_by_star_range(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        min_stars: int = 0,
        max_stars: int = 100,
        max_repos: Optional[int] = None,
        resume: bool = True,
    ) -> int:
        """
        Sammle Repositories in einem bestimmten Zeitraum und Star-Bereich.

        Args:
            start_date: Startdatum (optional)
            end_date: Enddatum (optional)
            min_stars: Minimale Anzahl von Stars (inklusive)
            max_stars: Maximale Anzahl von Stars (inklusive)
            max_repos: Maximale Anzahl zu sammelnder Repositories (optional)
            resume: Sammlung fortsetzen, falls unterbrochen
        Returns:
            Anzahl der gesammelten Repositories
        """
        from time import perf_counter

        collection_start_time = perf_counter()

        # Setup: Datums-Defaults
        now = datetime.now(tz.UTC)
        if end_date is None:
            end_date = now
        if start_date is None:
            start_date = now - timedelta(days=BATCH_CONFIG["repository_initial_period_days"])

        # Initialisiere neue oder setze unterbrochene Sammlung fort
        self._initialize_or_resume_star_range_collection(
            start_date, end_date, min_stars, max_stars, resume
        )
        total_collected = 0
        while True:
            period = self.state.get_current_period()
            if not period:
                logger.info("Keine weiteren Zeitperioden verfügbar")
                break
            period_start, period_end = period
            current_period_index = self.state.state.get("current_period_index", 0)
            total_periods = len(self.state.state.get("time_periods", []))
            show_period_progress(current_period_index, total_periods)
            logger.info(
                f"Sammle Repositories in der Periode {period_start} bis {period_end} (Stars {min_stars}..{max_stars})"
            )
            period_collected = self._collect_repositories_in_period_by_star_range(
                start_date=period_start,
                end_date=period_end,
                min_stars=min_stars,
                max_stars=max_stars,
                max_repos=max_repos,
            )
            total_collected += period_collected
            if max_repos and total_collected >= max_repos:
                show_max_repos_reached(max_repos)
                break
            if not self.state.next_period():
                logger.info("Alle Zeitperioden abgeschlossen")
                break
        show_collection_summary(total_collected)
        collection_end_time = perf_counter()
        collection_time = collection_end_time - collection_start_time
        print(
            f"\n\u23f0 Gesamtzeit: {collection_time:.2f} Sekunden für {total_collected} Repositories"
        )
        if total_collected > 0:
            print(
                f"   Durchschnitt gesamt: {collection_time / total_collected:.2f} Sekunden pro Repository"
            )
        print(f"   Reine Verarbeitungszeit: {self._total_processing_time:.2f} Sekunden")
        print(
            f"   Overhead (API, Suche, etc.): {collection_time - self._total_processing_time:.2f} Sekunden"
        )
        return total_collected

    def _process_batch_with_feature_flag(self, items: list) -> None:
        """Verarbeite einen Batch basierend auf Feature-Flag (parallel oder sequentiell).

        Args:
            items: Liste von Repository-Daten zum Verarbeiten
        """
        if ENABLE_PARALLEL_PROCESSING:
            logger.info(
                "Verarbeite Repository-Batch parallel (ENABLE_PARALLEL_PROCESSING aktiviert)"
            )
            self.process_repositories_parallel(items)
        else:
            logger.info("Verarbeite Repository-Batch sequentiell (Parallelisierung deaktiviert)")
            self._process_repositories_batch(items)

    def _check_star_range_pagination_end(
        self, items: list, page: int, per_page: int, collected_count: int, total_count: int
    ) -> bool:
        """Prüfe, ob Pagination für Star-Range-Collection beendet werden soll.

        Args:
            items: Zuletzt abgerufene Items
            page: Aktuelle Seitenzahl
            per_page: Items pro Seite
            collected_count: Anzahl gesammelter Repositories
            total_count: Gesamtanzahl verfügbarer Repositories

        Returns:
            True wenn Pagination beendet werden soll, sonst False
        """
        # GitHub API Limit: Max 1000 Ergebnisse
        if len(items) < per_page or page * per_page >= 1000:
            if collected_count > 0:
                if collected_count >= total_count:
                    show_collection_complete(total_count)
                else:
                    show_collection_progress(collected_count, total_count)
            return True
        return False

    def _collect_repositories_in_period_by_star_range(
        self,
        start_date: datetime,
        end_date: datetime,
        min_stars: int = 0,
        max_stars: int = 100,
        max_repos: Optional[int] = None,
    ) -> int:
        """
        Sammle Repositories in einer bestimmten Zeitperiode und Star-Range.
        Args:
            start_date: Startdatum
            end_date: Enddatum
            min_stars: Minimale Anzahl von Stars
            max_stars: Maximale Anzahl von Stars
            max_repos: Maximale Anzahl zu sammelnder Repositories (optional)
        Returns:
            Anzahl der gesammelten Repositories
        """
        page = self.state.get("current_period_page", 1)
        collected_count = 0
        total_count = None
        last_count = 0
        # Feste Seitengröße für die Search-API (z.B. 100, GitHub-Limit-konform)
        per_page = GITHUB_API["search_page_size"]
        while True:
            items, page_total_count, _incomplete = self._fetch_page_with_retry(
                start_date, end_date, min_stars, page, max_stars=max_stars
            )
            if total_count is None:
                total_count = page_total_count
            if not items:
                logger.info(f"Keine weiteren Repositories in dieser Periode (Seite {page})")
                if collected_count < total_count and collected_count > 0:
                    show_collection_progress(collected_count, total_count)
                break

            # Verarbeite Batch (parallel oder sequentiell)
            self._process_batch_with_feature_flag(items)
            collected_count += len(items)

            # State aktualisieren
            total_collected = self.state.get("repositories_collected", 0) + len(items)
            self.state.update(repositories_collected=total_collected, current_period_page=page)

            # Max-Repos-Limit prüfen
            if self._check_max_repos_reached(total_collected, max_repos, collected_count):
                return collected_count

            # Progress-Update (alle 100 Repos)
            if collected_count // 100 > last_count // 100 and total_count > 0:
                last_count = collected_count
                show_collection_progress(collected_count, total_count)

            # Pagination-Ende prüfen
            if self._check_star_range_pagination_end(
                items, page, per_page, collected_count, total_count
            ):
                break
            page += 1
            self.state.update(current_period_page=page)
        return collected_count

    def process_repositories_parallel(self, repositories_data: List[Dict[str, Any]]) -> int:
        """Parallele Verarbeitung von Repositories innerhalb eines API-Batches.

        Diese Methode orchestriert ausschließlich die Aufteilung in Chunks und die
        Verteilung auf Threads. Die eigentliche Verarbeitungslogik verbleibt in
        ``_process_repositories_batch``.

        Args:
            repositories_data: Liste von Repository-Daten aus der GitHub API.

        Returns:
            Anzahl der verarbeiteten Repositories (Summe über alle Chunks).
        """

        total_repos = len(repositories_data)
        if total_repos == 0:
            return 0

        # Bestimme die Chunk-Größe basierend auf dem AdaptiveBatchManager
        chunk_size = self.batch_manager.current_size or 100
        # Sanity-Clamp, um extreme Werte zu vermeiden
        if chunk_size < 10:
            chunk_size = 10
        if chunk_size > 500:
            chunk_size = 500

        # Für sehr kleine Batches lohnt sich Parallelisierung oft nicht
        if total_repos <= chunk_size:
            logger.info(
                "Verarbeite %d Repositories sequentiell (Batch kleiner oder gleich Chunk-Größe %d)",
                total_repos,
                chunk_size,
            )
            self._process_repositories_batch(repositories_data)
            return total_repos

        # Erzeuge Chunks basierend auf der aktuellen Ziel-Batchgröße
        chunks: List[List[Dict[str, Any]]] = [
            repositories_data[i : i + chunk_size] for i in range(0, total_repos, chunk_size)
        ]

        num_chunks = len(chunks)
        logger.info(
            "Processing %d repositories in parallel (%d chunks) with %d threads",
            total_repos,
            num_chunks,
            MAX_WORKER_THREADS,
        )

        processed_total = 0

        # Phase 4.1: Prometheus-Metrik für aktive Threads
        update_active_threads(MAX_WORKER_THREADS)

        try:
            with ThreadPoolExecutor(max_workers=MAX_WORKER_THREADS) as executor:
                future_to_chunk_size = {
                    executor.submit(self._process_repositories_batch, chunk): len(chunk)
                    for chunk in chunks
                }

                for future in as_completed(future_to_chunk_size):
                    chunk_len = future_to_chunk_size[future]
                    try:
                        # _process_repositories_batch gibt aktuell None zurück;
                        # wir verlassen uns daher auf chunk_len als Näherung.
                        future.result()
                        processed_total += chunk_len
                    except Exception as exc:  # noqa: BLE001
                        logger.error(
                            "Fehler bei der parallelen Verarbeitung eines Repository-Chunks (%d Elemente): %s",
                            chunk_len,
                            exc,
                        )
        finally:
            # Phase 4.1: Threads beendet
            update_active_threads(0)

        return processed_total

    def _get_total_repositories_in_timeframe(
        self, start_date: datetime, end_date: datetime, min_stars: int = 0, max_stars: int = None
    ) -> int:
        """
        Ermittle die Gesamtanzahl der Repositories im angegebenen Zeitraum und Star-Bereich.
        Args:
            start_date: Startdatum
            end_date: Enddatum
            min_stars: Minimale Anzahl von Stars
            max_stars: Maximale Anzahl von Stars (optional)
        Returns:
            Gesamtanzahl der Repositories
        """
        try:
            results = self._search_repositories_in_period(
                start_date=start_date,
                end_date=end_date,
                min_stars=min_stars,
                max_stars=max_stars,
                page=1,
            )
            if "total_count" in results:
                return results["total_count"]
            return 0
        except Exception as e:
            logger.error(f"Fehler bei der Ermittlung der Gesamtanzahl der Repositories: {e}")
            return 0

    def get_data_volume_estimation(
        self,
        start_date: datetime,
        end_date: datetime,
        min_stars: int = 0,
    ) -> Dict[str, int]:
        """Schätze das Datenvolumen für einen Zeitraum vor der Partitionierung.

        Diese Methode führt einen minimalen Search-API-Aufruf aus (per_page=1),
        um `total_count` zu bestimmen, und leitet daraus eine empfohlene
        Periodenzahl ab.

        Die Empfehlung basiert auf einem konservativen Ziel von 800 Repositories
        pro Periode (GitHub-Search-Limit liegt bei ~1000 Ergebnissen pro Query).

        Args:
            start_date: Startdatum des Zeitraums.
            end_date: Enddatum des Zeitraums.
            min_stars: Untere Grenze der Star-Filterung.

        Returns:
            Dictionary mit `total_count`, `recommended_periods` und
            `repos_per_period`.
        """

        # Verwende dieselbe Query-Strategie wie _search_repositories_in_period,
        # aber mit per_page=1, um Traffic zu minimieren.
        if start_date > end_date:
            raise ValueError("start_date must be <= end_date")

        query = f"created:{start_date.isoformat()}..{end_date.isoformat()} stars:>={min_stars}"

        results = self.github.search_repositories(
            query=query,
            sort="stars",
            order="desc",
            per_page=1,
            page=1,
        )

        total_count = int(results.get("total_count", 0))

        if total_count <= 0:
            return {
                "total_count": 0,
                "recommended_periods": 1,
                "repos_per_period": 0,
            }

        # Sicherheitsziel: 800 Repositories pro Periode (unterhalb des 1000er-Limits)
        recommended_periods = max(1, ceil(total_count / 800))
        repos_per_period = ceil(total_count / recommended_periods)

        return {
            "total_count": total_count,
            "recommended_periods": recommended_periods,
            "repos_per_period": repos_per_period,
        }

    def _collect_contributors_metadata_batch(
        self,
        contributor_logins: List[str],
        contributor_ids: Optional[List[Optional[int]]] = None,
        batch_id: Optional[str] = None,
    ) -> None:
        """
        Sammelt Metadaten für mehrere Contributors in einem Batch.

        Args:
            contributor_logins: Liste der GitHub-Benutzernamen der Contributors
            contributor_ids: Optionale Liste der GitHub-IDs (parallel zu logins)
            batch_id: Optionale Batch-Trace-ID für konsistentes Logging
        """
        # Verwende den Owner-Processor für die Verarbeitung der Contributors
        self.owner_processor.process_contributors_batch(
            contributor_logins, contributor_ids, batch_id=batch_id
        )

    def _extract_owners_from_repositories(
        self,
        repositories_data: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Extrahiere Contributor- und Organisations-Daten aus einem Repository-Batch.

        **Dual Extraction Strategy:**
        - ALLE Owner (User UND Organization) werden als Contributors extrahiert,
          da `repositories.owner_id` ein FK auf `contributors.id` ist.
        - Organizations werden ZUSÄTZLICH in die organizations-Liste aufgenommen,
          um `repositories.organization_id` korrekt setzen zu können.

        Duplikate werden im Python-Code entfernt, um unnötige Datenverarbeitung
        und -übergabe zu vermeiden.

        Args:
            repositories_data: Liste von Repository-Daten aus der GitHub API.

        Returns:
            Tupel (contributors, organizations) mit je deduplizierten Dictionaries.
        """

        contributor_by_login: Dict[str, Dict[str, Any]] = {}
        organization_by_login: Dict[str, Dict[str, Any]] = {}

        for repo_data in repositories_data:
            owner_data = repo_data.get("owner") or {}
            owner_login = owner_data.get("login")
            owner_type = owner_data.get("type")

            # IMMER: Jeden Owner als Contributor erfassen (für owner_id FK)
            if owner_login and owner_login not in contributor_by_login:
                contributor_by_login[owner_login] = owner_data

            # ZUSÄTZLICH: Organizations auch in organizations-Tabelle erfassen
            if owner_type == "Organization" and owner_login:
                if owner_login not in organization_by_login:
                    organization_by_login[owner_login] = owner_data

            # Optionale organisationsspezifische Daten direkt am Repository
            # (für organization_id, falls Repo zu einer Org gehört)
            org_data = repo_data.get("organization") or {}
            org_login = org_data.get("login")
            if org_login:
                # Org auch als Contributor erfassen (falls sie Owner eines anderen Repos ist)
                if org_login not in contributor_by_login:
                    contributor_by_login[org_login] = org_data
                # Und in organizations für organization_id
                if org_login not in organization_by_login:
                    organization_by_login[org_login] = org_data

        return list(contributor_by_login.values()), list(organization_by_login.values())

    def _normalize_repositories_for_db(
        self,
        repositories_data: List[Dict[str, Any]],
        contributor_mapping: Dict[str, int],
        organization_mapping: Dict[str, int],
    ) -> List[Dict[str, Any]]:
        """Normalisiere Repository-Daten für den Bulk-Insert im DB-Layer.

        Diese Methode konvertiert rohe GitHub-Repository-Daten in Dictionaries,
        die direkt auf das ``Repository``-Model abgebildet werden können und
        von ``bulk_upsert_repositories`` erwartet werden.

        **Wichtig:** `owner_id` wird IMMER aus `contributor_mapping` geholt,
        da alle Owner (User UND Organizations) als Contributors angelegt werden.
        `organization_id` wird separat aus `organization_mapping` geholt.

        Args:
            repositories_data: Liste von Repository-Daten aus der GitHub API.
            contributor_mapping: Mapping login -> Contributor-ID (enthält ALLE Owner).
            organization_mapping: Mapping login -> Organization-ID.

        Returns:
            Liste normalisierter Repository-Dictionaries.
        """

        normalized: List[Dict[str, Any]] = []
        skipped_no_login = 0
        skipped_no_mapping = 0

        for repo_data in repositories_data:
            owner_data = repo_data.get("owner") or {}
            owner_login = owner_data.get("login")

            if not owner_login:
                # Ohne Login können wir keinen Owner zuordnen
                logger.warning(
                    "Überspringe Repository %s ohne gültigen Owner-Login",
                    repo_data.get("full_name"),
                )
                skipped_no_login += 1
                continue

            # owner_id IMMER aus contributor_mapping (alle Owner sind Contributors)
            owner_id = contributor_mapping.get(owner_login)

            if owner_id is None:
                # Safety Check: Sollte nach Dual Extraction nicht mehr vorkommen
                logger.warning(
                    "Überspringe Repository %s: Owner '%s' nicht in contributor_mapping "
                    "(unerwarteter Zustand nach Dual Extraction)",
                    repo_data.get("full_name"),
                    owner_login,
                )
                skipped_no_mapping += 1
                continue

            # organization_id separat aus organization_mapping
            org_id: Optional[int] = None
            org_data = repo_data.get("organization") or {}
            org_login = org_data.get("login")
            if org_login:
                org_id = organization_mapping.get(org_login)

            normalized.append(
                {
                    "id": repo_data["id"],
                    "name": repo_data["name"],
                    "full_name": repo_data["full_name"],
                    "owner_id": owner_id,
                    "organization_id": org_id,
                    "description": repo_data.get("description"),
                    "homepage": repo_data.get("homepage"),
                    "language": repo_data.get("language"),
                    "private": repo_data.get("private", False),
                    "fork": repo_data.get("fork", False),
                    "default_branch": repo_data.get("default_branch"),
                    "size": repo_data.get("size"),
                    "stargazers_count": repo_data.get("stargazers_count", 0),
                    "watchers_count": repo_data.get("watchers_count", 0),
                    "forks_count": repo_data.get("forks_count", 0),
                    "open_issues_count": repo_data.get("open_issues_count", 0),
                    "contributors_count": repo_data.get("contributors_count"),
                    "commits_count": repo_data.get("commits_count"),
                    "pull_requests_count": repo_data.get("pull_requests_count"),
                    "created_at": repo_data.get("created_at"),
                    "updated_at": repo_data.get("updated_at"),
                    "pushed_at": repo_data.get("pushed_at"),
                }
            )

        # Log-Zusammenfassung für Debugging
        if skipped_no_login > 0 or skipped_no_mapping > 0:
            logger.warning(
                "Normalisierung: %d Repos übersprungen (kein Login: %d, kein Mapping: %d)",
                skipped_no_login + skipped_no_mapping,
                skipped_no_login,
                skipped_no_mapping,
            )

        return normalized

    def _validate_repositories(
        self, repositories_data: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Validiere Repositories mit Pydantic und filtere invalide Einträge.

        Args:
            repositories_data: Liste von rohen Repository-Daten aus der GitHub API

        Returns:
            Liste von validierten und bereinigten Repository-Dicts
        """
        batch_size = len(repositories_data)
        valid_repos: List[RepositorySchema] = []
        invalid_count = 0

        for raw_item in repositories_data:
            try:
                repo_schema = RepositorySchema(**raw_item)
                valid_repos.append(repo_schema)
                # Phase 4.1: Prometheus-Metrik für valides Repository
                record_repository_processed(status="valid")
            except ValidationError as e:
                invalid_count += 1
                repo_id = raw_item.get("id", "unknown")
                repo_name = raw_item.get("full_name", "unknown")
                logger.warning(
                    f"Skipping invalid repository {repo_name} (ID={repo_id}): {e.error_count()} validation error(s)"
                )
                # Phase 4.1: Prometheus-Metrik für invalides Repository
                record_repository_processed(status="invalid")
                # Optional: Detaillierte Fehler nur bei Debug-Level
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Validation errors for {repo_name}: {e}")

        # Statistik
        if invalid_count > 0:
            logger.warning(
                f"⚠️  Validation filtered out {invalid_count}/{batch_size} repositories ({invalid_count/batch_size*100:.1f}%)"
            )
        else:
            logger.info(f"✓ All {batch_size} repositories passed validation")

        # Konvertiere validierte Schemas zurück zu Dicts für bestehende Pipeline
        # model_dump(mode='json') stellt sicher, dass alle Typen JSON-serialisierbar sind
        clean_repositories_data = [repo.model_dump(mode="json") for repo in valid_repos]

        return clean_repositories_data

    def _validate_and_filter_batch(
        self, repositories_data: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Wrapper um die Validierungs-Firewall für einzelne Batches.

        Diese Methode existiert primär, um die Komplexität von
        ``_process_repositories_batch`` zu reduzieren und die
        Validierungsschritte klar zu kapseln.
        """

        return self._validate_repositories(repositories_data)

    def _analyze_owner_distribution(
        self, clean_repositories_data: List[Dict[str, Any]]
    ) -> Tuple[int, int]:
        """Zähle User- und Organisations-Owner für Logging-Zwecke."""

        user_owners = 0
        org_owners = 0
        for repo in clean_repositories_data:
            owner = repo.get("owner") or {}
            owner_type = owner.get("type")
            if owner_type == "User":
                user_owners += 1
            elif owner_type == "Organization":
                org_owners += 1

        return user_owners, org_owners

    def _process_owners_for_batch(
        self,
        clean_repositories_data: List[Dict[str, Any]],
        batch_id: str,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Extrahiere und verarbeite Owner-Daten (Contributors & Organizations) für einen Batch."""

        # Dual Extraction: Alle Owner (User+Org) → contributors, Orgs zusätzlich → organizations
        contributors, organizations = self._extract_owners_from_repositories(
            clean_repositories_data
        )

        logger.info(
            "[%s] Dual Extraction: %d Owners (Base, inkl. Org-Owner), %d Organizations",
            batch_id,
            len(contributors),
            len(organizations),
        )

        contributor_logins = [c["login"] for c in contributors if c.get("login")]
        contributor_ids = [c.get("id") for c in contributors if c.get("login")]
        if contributor_logins:
            self._collect_contributors_metadata_batch(
                contributor_logins, contributor_ids, batch_id=batch_id
            )

        if organizations:
            self.owner_processor.process_organizations_batch(organizations, batch_id=batch_id)

        return contributors, organizations

    def _persist_batch_to_database(
        self,
        clean_repositories_data: List[Dict[str, Any]],
        contributors: List[Dict[str, Any]],
        organizations: List[Dict[str, Any]],
        batch_id: str,
    ) -> int:
        """Führe alle Bulk-DB-Operationen für einen Batch aus und gib die Anzahl verarbeiteter Repositories zurück."""

        processed_count = 0
        try:
            with session_scope() as session:
                contributor_mapping = self.db.bulk_upsert_contributors(
                    contributors,
                    session=session,
                    batch_id=batch_id,
                )

                organization_mapping: Dict[str, int] = {}
                if organizations:
                    organization_mapping = self.db.bulk_upsert_organizations(
                        organizations,
                        session=session,
                        batch_id=batch_id,
                    )

                normalized_repos = self._normalize_repositories_for_db(
                    clean_repositories_data,
                    contributor_mapping,
                    organization_mapping,
                )

                if not normalized_repos:
                    logger.warning(
                        "Keine Repositories zum Bulk-Upsert nach Normalisierung vorhanden; Batch wird übersprungen"
                    )
                    return 0

                processed_count = self.db.bulk_upsert_repositories(
                    normalized_repos,
                    session=session,
                    batch_id=batch_id,
                )
        except Exception as e:  # noqa: BLE001
            logger.error(f"Fehler bei der Bulk-Verarbeitung von Repositories: {e}")
            return 0

        return processed_count

    def _finalize_batch_metrics_and_logging(
        self,
        batch_id: str,
        batch_size: int,
        processed_count: int,
        batch_start_time: float,
    ) -> None:
        """Abschluss eines Batches: Zeitmessung, Metriken, Logging und Rate-Limit-Monitoring."""

        batch_end_time = perf_counter()
        batch_processing_time = batch_end_time - batch_start_time
        avg_time_per_repo = batch_processing_time / max(1, processed_count)

        # Akkumuliere reine Verarbeitungszeit über alle Batches
        self._total_processing_time += batch_processing_time

        # Dynamische Anpassung der Batch-Größe (Phase 2.2)
        self.batch_manager.update(batch_processing_time, processed_count)

        # Phase 4.1: Prometheus-Metriken aktualisieren
        batch_processing_seconds.observe(batch_processing_time)
        update_batch_size(self.batch_manager.current_size)

        # Zeige Performance-Informationen für den Batch an
        # Wichtig: processed_count == batch_size bedeutet 0% Verlust (Ziel erreicht!)
        loss_pct = ((batch_size - processed_count) / batch_size * 100) if batch_size > 0 else 0

        # Konsistentes Logging mit batch_id
        logger.info(
            "[%s] Batch completed: %s processed (raw=%s, time=%.2fs, avg=%.2fs/repo, loss=%.1f%%)",
            batch_id,
            processed_count,
            batch_size,
            batch_processing_time,
            avg_time_per_repo,
            loss_pct,
        )

        # Menschlich lesbare Konsolen-Ausgabe mit Trace-ID
        print(
            f"\n[{batch_id}] Batch mit {processed_count} von {batch_size} Repositories verarbeitet in {batch_processing_time:.2f} Sekunden"
        )
        if loss_pct > 0:
            print(f"   Verlust: {loss_pct:.1f}% ({batch_size - processed_count} Repos)")
        print(f"   Durchschnitt im Batch: {avg_time_per_repo:.2f} Sekunden pro Repository")

        print()  # Extra blank line to visually separate consecutive batches

        # Überwache das Rate-Limit nach dem Batch
        self.api.monitor_rate_limit()

    def _process_repositories_batch(self, repositories_data: List[Dict[str, Any]]) -> None:
        """
        Verarbeite mehrere Repositories in einem Batch.

        Args:
            repositories_data: Liste von Repository-Daten aus der GitHub API.
        """
        # Starte Zeitmessung für den Batch
        batch_start_time = perf_counter()
        batch_size = len(repositories_data)

        # Generiere eine Trace-ID für dieses Batch (für korrelierbares Logging)
        batch_id = uuid.uuid4().hex[:6]
        logger.info(f"[{batch_id}] Starte Verarbeitung von Batch mit {batch_size} Roh-Repositories")

        # Überwache das Rate-Limit vor dem Batch
        rate_limit_info = self.api.monitor_rate_limit()
        if rate_limit_info.get("has_critical", False):
            logger.warning("Kritisches Rate-Limit vor Repository-Batch-Verarbeitung erkannt")
            print("\n[Warnung] Kritisches Rate-Limit vor Repository-Batch-Verarbeitung!\n")

        # === PHASE 3.1: PYDANTIC VALIDIERUNGS-FIREWALL ===
        clean_repositories_data = self._validate_and_filter_batch(repositories_data)

        # Falls alle Repos invalid sind, breche ab
        if not clean_repositories_data:
            logger.error(
                f"[{batch_id}] Keine validen Repositories im Batch - überspringe Verarbeitung"
            )
            return

        user_owners, org_owners = self._analyze_owner_distribution(clean_repositories_data)

        logger.info(
            f"[{batch_id}] Processing {len(clean_repositories_data)} Repositories "
            f"(Owners: {user_owners} Users, {org_owners} Organizations)"
        )

        contributors, organizations = self._process_owners_for_batch(
            clean_repositories_data, batch_id
        )

        processed_count = self._persist_batch_to_database(
            clean_repositories_data, contributors, organizations, batch_id
        )

        self._finalize_batch_metrics_and_logging(
            batch_id=batch_id,
            batch_size=batch_size,
            processed_count=processed_count,
            batch_start_time=batch_start_time,
        )

    def _should_split_period(
        self, start_date: datetime, end_date: datetime, min_stars: int
    ) -> Optional[int]:
        """Prüfe, ob eine Periode zu viele Ergebnisse hat und aufgeteilt werden muss.

        Args:
            start_date: Startdatum der Periode
            end_date: Enddatum der Periode
            min_stars: Minimale Anzahl von Stars

        Returns:
            total_count wenn Periode zu groß ist (>1000 Ergebnisse), sonst None
        """
        _items, total_count, _incomplete = self._fetch_page_with_retry(
            start_date, end_date, min_stars, page=1
        )
        if total_count > GITHUB_API["search_max_results"]:
            return total_count
        return None

    def _split_and_reschedule_period(
        self, start_date: datetime, end_date: datetime, total_count: int
    ) -> int:
        """Teile eine zu große Periode in Subperioden und aktualisiere den State.

        Args:
            start_date: Startdatum der zu großen Periode
            end_date: Enddatum der zu großen Periode
            total_count: Anzahl gefundener Repositories (für Logging)

        Returns:
            0 (signalisiert, dass die Periode aufgeteilt wurde)
        """
        show_period_too_large(total_count)

        # Teile die Periode auf
        new_periods = self._adjust_period_size((start_date, end_date), total_count)

        # Hole aktuellen State
        current_period_index = self.state.state.get("current_period_index", 0)
        all_periods = self.state.state.get("time_periods", [])

        # Konvertiere neue Perioden ins State-Format
        new_period_dicts = [
            {"start": start.isoformat(), "end": end.isoformat()} for start, end in new_periods
        ]

        # Ersetze aktuelle Periode durch Subperioden
        updated_periods = (
            all_periods[:current_period_index]
            + new_period_dicts
            + all_periods[current_period_index + 1 :]
        )

        # State aktualisieren
        self.state.state["time_periods"] = updated_periods
        self.state.state["current_period_index"] = current_period_index
        self.state.state["current_period_page"] = 1
        self.state.save()

        return 0

    def _fetch_page_with_retry(
        self,
        start_date: datetime,
        end_date: datetime,
        min_stars: int,
        page: int,
        max_stars: Optional[int] = None,
    ) -> tuple[list, int, bool]:
        """Hole eine Seite mit Retry-Logik bei Rate-Limit/API-Fehlern.

        Args:
            start_date: Startdatum
            end_date: Enddatum
            min_stars: Minimale Anzahl von Stars
            page: Seitennummer
            max_stars: Maximale Anzahl von Stars (nur im Star-Range-Modus)

        Returns:
            Tuple aus (items, total_count, incomplete). `incomplete` ist True, wenn
            GitHub die Antwort als möglicherweise unvollständig markiert hat.

        Raises:
            GitHubAuthenticationError: Sofort, ohne Wiederholung (kein Token ist gültig).
            GitHubAPIError: Nach dem letzten erfolglosen Versuch.
        """
        max_retries = 3

        for attempt in range(max_retries):
            try:
                # Rate-Limit-Überwachung
                rate_limit_info = self.api.monitor_rate_limit()
                if rate_limit_info.get("has_critical", False):
                    time.sleep(2)

                # API-Call
                results = self._search_repositories_in_period(
                    start_date=start_date,
                    end_date=end_date,
                    min_stars=min_stars,
                    page=page,
                    max_stars=max_stars,
                )

                items = results.get("items", [])
                total_count = results.get("total_count", 0)
                incomplete = bool(results.get("incomplete_results", False))

                # Bei erster erfolgreicher Page: Logging + UI-Update
                if page == 1 and total_count > 0:
                    logger.info(
                        f"Gefunden: {total_count} Repositories in der Periode {start_date} bis {end_date}"
                    )
                    show_repositories_found(total_count)

                return items, total_count, incomplete

            except GitHubAuthenticationError:
                # Kein gültiges Token mehr: Wiederholen hilft nicht
                raise

            except GitHubRateLimitError:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Rate-Limit erreicht (Versuch {attempt + 1}/{max_retries}). Warte..."
                    )
                    print(
                        "\n[RateLimit] Rate-Limit erreicht. Automatischer Neuversuch nach Reset.\n"
                    )
                    time.sleep(BATCH_CONFIG["repository_rate_limit_sleep_seconds"])
                else:
                    raise

            except GitHubAPIError as e:
                if attempt < max_retries - 1:
                    logger.error(f"API-Fehler (Versuch {attempt + 1}/{max_retries}): {e}")
                    print(f"\n[Fehler] GitHub API-Fehler: {e}\n")
                    time.sleep(BATCH_CONFIG["repository_api_error_sleep_seconds"])
                else:
                    raise

        raise GitHubAPIError("Repository-Suche fehlgeschlagen")  # pragma: no cover

    def _check_max_repos_reached(
        self, total_collected: int, max_repos: Optional[int], collected_count: int
    ) -> bool:
        """Prüfe, ob das Maximum an Repositories erreicht wurde.

        Args:
            total_collected: Gesamtanzahl bisher gesammelter Repositories
            max_repos: Maximale Anzahl (optional)
            collected_count: In dieser Periode gesammelte Repositories

        Returns:
            True wenn Limit erreicht wurde, sonst False
        """
        if max_repos and total_collected >= max_repos:
            show_max_repos_reached(max_repos)
            return True
        return False

    def _check_pagination_end(
        self, items: list, page: int, collected_count: int, total_count: Optional[int]
    ) -> bool:
        """Prüfe, ob Pagination beendet werden soll und zeige finale UI-Updates.

        Args:
            items: Zuletzt abgerufene Items
            page: Aktuelle Seitenzahl
            collected_count: Anzahl gesammelter Repositories
            total_count: Gesamtanzahl verfügbarer Repositories (optional)

        Returns:
            True wenn Pagination beendet werden soll, sonst False
        """
        # GitHub API Limit: Max 1000 Ergebnisse (10 Seiten à 100 Items)
        if len(items) < 100 or page >= 10:
            if collected_count > 0:
                if total_count and collected_count >= total_count:
                    show_collection_complete(total_count)
                else:
                    show_collection_progress(collected_count, total_count or collected_count)
                    if page >= 10:
                        show_api_limit_reached()
            return True
        return False

    def _paginate_and_collect(
        self, start_date: datetime, end_date: datetime, min_stars: int, max_repos: Optional[int]
    ) -> int:
        """Paginiere durch Suchergebnisse und sammle Repositories.

        Args:
            start_date: Startdatum
            end_date: Enddatum
            min_stars: Minimale Anzahl von Stars
            max_repos: Maximale Anzahl zu sammelnder Repositories (optional)

        Returns:
            Anzahl der gesammelten Repositories
        """
        page = self.state.get("current_period_page", 1)
        collected_count = 0
        last_count = 0
        total_count = None

        while True:
            # Hole Seite mit Retry-Logik
            items, page_total_count, _incomplete = self._fetch_page_with_retry(
                start_date, end_date, min_stars, page
            )

            # Aktualisiere total_count bei erster Seite
            if total_count is None and page_total_count is not None:
                total_count = page_total_count

            # Keine Items mehr → Fertig
            if not items:
                logger.info(f"Keine weiteren Repositories in dieser Periode (Seite {page})")
                if 0 < collected_count < total_count:
                    show_collection_progress(collected_count, total_count)
                break

            # Verarbeite Batch
            self._process_repositories_batch(items)
            collected_count += len(items)

            # State aktualisieren
            total_collected = self.state.get("repositories_collected", 0) + len(items)
            self.state.update(repositories_collected=total_collected, current_period_page=page)

            # Max-Repos-Limit prüfen
            if self._check_max_repos_reached(total_collected, max_repos, collected_count):
                return collected_count

            # Progress-Update (alle 100 Repos)
            if collected_count // 100 > last_count // 100 and total_count and total_count > 0:
                last_count = collected_count
                show_collection_progress(collected_count, total_count)

            # Pagination-Ende prüfen
            if self._check_pagination_end(items, page, collected_count, total_count):
                break

            # Nächste Seite
            page += 1
            self.state.update(current_period_page=page)
            time.sleep(1)  # API Rate-Limiting

        return collected_count

    def _collect_repositories_in_period(
        self,
        start_date: datetime,
        end_date: datetime,
        min_stars: int = 0,
        max_repos: Optional[int] = None,
    ) -> int:
        """Sammle Repositories in einer bestimmten Zeitperiode.

        Orchestriert Period-Splitting, Pagination und Collection.

        Args:
            start_date: Startdatum
            end_date: Enddatum
            min_stars: Minimale Anzahl von Stars
            max_repos: Maximale Anzahl zu sammelnder Repositories (optional)

        Returns:
            Anzahl der gesammelten Repositories
        """
        # Prüfe, ob Periode zu groß ist und aufgeteilt werden muss
        split_total_count = self._should_split_period(start_date, end_date, min_stars)
        if split_total_count:
            return self._split_and_reschedule_period(start_date, end_date, split_total_count)

        # Normale Pagination und Collection
        return self._paginate_and_collect(start_date, end_date, min_stars, max_repos)

    def _setup_collection_dates(
        self, start_date: Optional[datetime], end_date: Optional[datetime]
    ) -> tuple[datetime, datetime]:
        """Setze Standard-Werte für Start- und Enddatum und stelle UTC sicher.

        Args:
            start_date: Startdatum (optional)
            end_date: Enddatum (optional)

        Returns:
            Tuple aus (start_date, end_date) mit UTC-Timezone
        """
        # Standardwerte
        if end_date is None:
            end_date = datetime.now(tz.UTC)
        if start_date is None:
            start_date = end_date - timedelta(days=BATCH_CONFIG["repository_initial_period_days"])

        # UTC-Timezone sicherstellen
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=tz.UTC)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=tz.UTC)

        return start_date, end_date

    def _initialize_or_resume_collection(
        self, start_date: datetime, end_date: datetime, min_stars: int, resume: bool
    ) -> None:
        """Initialisiere eine neue Sammlung oder setze eine unterbrochene fort.

        Args:
            start_date: Startdatum
            end_date: Enddatum
            min_stars: Minimale Anzahl von Stars
            resume: Ob unterbrochene Sammlung fortgesetzt werden soll
        """
        if resume and self.state.get("time_periods") and self.state.get_current_period():
            # Fortsetzen einer unterbrochenen Sammlung
            logger.info("Setze unterbrochene Sammlung fort")

            state_start_date = datetime.fromisoformat(self.state.get("start_date"))
            state_end_date = datetime.fromisoformat(self.state.get("end_date"))

            total_repos = self._get_total_repositories_in_timeframe(
                state_start_date, state_end_date, min_stars
            )
            if total_repos > 0:
                print(
                    f"\nInsgesamt gefundene Repositories im Zeitraum {state_start_date.strftime('%Y-%m-%d')} bis {state_end_date.strftime('%Y-%m-%d')} mit mindestens {min_stars} Stars: {total_repos}"
                )
        else:
            # Neue Sammlung starten
            logger.info(f"Starte neue Sammlung von {start_date} bis {end_date}")

            total_repos = self._get_total_repositories_in_timeframe(start_date, end_date, min_stars)
            if total_repos > 0:
                print(
                    f"\nInsgesamt gefundene Repositories im Zeitraum {start_date.strftime('%Y-%m-%d')} bis {end_date.strftime('%Y-%m-%d')} mit mindestens {min_stars} Stars: {total_repos}"
                )

            # Berechne Zeitperioden und initialisiere State
            periods = self._calculate_time_periods(start_date, end_date)
            self.state.reset(start_date, end_date)
            self.state.set_time_periods(periods)

    def collect_repositories(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        min_stars: int = 0,
        max_repos: Optional[int] = None,
        resume: bool = True,
    ) -> int:
        """
        Sammle Repositories in einem bestimmten Zeitraum.

        Args:
            start_date: Startdatum (optional, Standard: vor einem Monat)
            end_date: Enddatum (optional, Standard: jetzt)
            min_stars: Minimale Anzahl von Stars
            max_repos: Maximale Anzahl zu sammelnder Repositories (optional)
            resume: Sammlung fortsetzen, falls unterbrochen

        Returns:
            Anzahl der gesammelten Repositories
        """
        # Starte Zeitmessung und Performance-Tracking
        collection_start_time = perf_counter()
        self.__class__._total_processing_time = 0.0
        self.__class__._repositories_processed = 0

        # Setup: Datums-Defaults und UTC-Konvertierung
        start_date, end_date = self._setup_collection_dates(start_date, end_date)

        # Initialisiere neue oder setze unterbrochene Sammlung fort
        self._initialize_or_resume_collection(start_date, end_date, min_stars, resume)

        total_collected = 0

        # Sammle Repositories in jeder Zeitperiode
        while True:
            # Hole die aktuelle Periode
            period = self.state.get_current_period()
            if not period:
                logger.info("Keine weiteren Zeitperioden verfügbar")
                break

            period_start, period_end = period
            # Hole die aktuelle Periode und Gesamtanzahl der Perioden
            current_period_index = self.state.state.get("current_period_index", 0)
            total_periods = len(self.state.state.get("time_periods", []))

            # Zeige den Periodenfortschritt an
            show_period_progress(current_period_index, total_periods)
            logger.info(f"Sammle Repositories in der Periode {period_start} bis {period_end}")

            # Sammle Repositories in dieser Periode
            period_collected = self._collect_repositories_in_period(
                start_date=period_start,
                end_date=period_end,
                min_stars=min_stars,
                max_repos=max_repos,
            )

            total_collected += period_collected

            # Prüfe, ob wir das Maximum erreicht haben
            if max_repos and total_collected >= max_repos:
                # Zeige eine Meldung an, dass die maximale Anzahl erreicht wurde
                show_max_repos_reached(max_repos)
                break

            # Gehe zur nächsten Periode
            if not self.state.next_period():
                logger.info("Alle Zeitperioden abgeschlossen")
                break

        # Zeige eine Zusammenfassung der gesamten Sammlung an
        show_collection_summary(total_collected)

        # Ende der Zeitmessung für den gesamten Sammelprozess
        collection_end_time = perf_counter()
        collection_time = collection_end_time - collection_start_time

        # Zeige Performance-Informationen für den gesamten Sammelprozess an
        print(
            f"\n\u23f0 Gesamtzeit: {collection_time:.2f} Sekunden für {total_collected} Repositories"
        )
        if total_collected > 0:
            print(
                f"   Durchschnitt gesamt: {collection_time / total_collected:.2f} Sekunden pro Repository"
            )
        print(f"   Reine Verarbeitungszeit: {self.__class__._total_processing_time:.2f} Sekunden")
        print(
            f"   Overhead (API, Suche, etc.): {collection_time - self.__class__._total_processing_time:.2f} Sekunden"
        )

        return total_collected
