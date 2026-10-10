"""
Owner-Verarbeitungsmodul für GitHub Data Collector.

Dieses Modul enthält Funktionen und Klassen zur Verarbeitung von
Owner-Metadaten (Contributor und Organization).
"""

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from github_collector.api.github_api import GitHubAPI
from github_collector.database.database import GitHubDatabase
from github_collector.utils.performance_tracker import PerformanceTracker

logger = logging.getLogger(__name__)


class OwnerProcessor:
    """
    Verarbeitet Owner-Metadaten (Contributor und Organization).

    Diese Klasse kapselt die Logik zur Verarbeitung von Owner-Metadaten,
    einschließlich Caching, Batch-Verarbeitung und Performance-Tracking.
    """

    def __init__(
        self,
        api: GitHubAPI,
        db: GitHubDatabase,
        performance_tracker: Optional[PerformanceTracker] = None,
    ):
        """
        Initialisiert den Owner-Processor.

        Args:
            api: GitHub API Client
            db: Datenbankverbindung
            performance_tracker: Performance-Tracker (optional)
        """
        self.api = api
        self.db = db
        self.performance_tracker = performance_tracker

        # Caches für Owner-Daten
        self._org_cache: Dict[str, Dict[str, Any]] = {}
        self._contributor_cache: Dict[str, Dict[str, Any]] = {}

        # Sets für die Erkennung von Duplikaten im aktuellen Batch
        self._batch_owners: Set[str] = set()

        # Bekannte Owner (bereits in der Datenbank) - nach normalisiertem Login (lowercase)
        self._known_owners: Set[str] = set()
        # Bekannte Owner-IDs (für Login-Änderungen)
        self._known_owner_ids: Set[int] = set()

        # Initialisiere die bekannten Owner aus der Datenbank
        self._preload_known_owners()

    def reset_batch_tracking(self) -> None:
        """Setzt die Batch-Tracking-Daten zurück."""
        self._batch_owners = set()

    def _preload_known_owners(self) -> None:
        """
        Lädt bekannte Owner aus der Datenbank, um redundante API-Aufrufe zu vermeiden.
        Diese Methode wird beim Initialisieren des OwnerProcessors aufgerufen.
        """
        try:
            # Lade bekannte Contributors (Logins und IDs)
            contributor_logins = self.db.get_all_contributor_logins()
            for login in contributor_logins:
                if login:
                    # Logins konsequent in lowercase normalisieren (Pydantic-Logik)
                    self._known_owners.add(login.lower())

            contributor_ids = self.db.get_all_contributor_ids()
            for cid in contributor_ids:
                self._known_owner_ids.add(cid)

            # Lade bekannte Organisationen (Logins und IDs)
            org_logins = self.db.get_all_organization_logins()
            for login in org_logins:
                if login:
                    self._known_owners.add(login.lower())

            org_ids = self.db.get_all_organization_ids()
            for oid in org_ids:
                self._known_owner_ids.add(oid)

            logger.info(
                f"Vorgeladen: {len(self._known_owners)} bekannte Owner (Logins), "
                f"{len(self._known_owner_ids)} bekannte Owner (IDs)"
            )
        except Exception as e:
            logger.warning(f"Fehler beim Vorladen bekannter Owner: {e}")
            # Fehler beim Vorladen sollten den Prozess nicht stoppen

    def _is_owner_in_batch(
        self,
        normalized_login: str,
        owner_login: str,
        store_normalized: bool,
        owner_type_label: str,
    ) -> bool:
        """Prüfe, ob ein Owner bereits im aktuellen Batch verarbeitet wurde.

        Gibt True zurück, wenn der Owner bereits gesehen wurde (inkl. Logging
        und optionalem Performance-Tracking), sonst False und fügt ihn hinzu.
        """

        key = normalized_login if store_normalized else owner_login
        if key in self._batch_owners:
            if self.performance_tracker:
                self.performance_tracker.record_duplicate_owner_in_batch()
            logger.debug(
                f"{owner_type_label} {owner_login} wurde bereits im aktuellen Batch verarbeitet"
            )
            return True

        self._batch_owners.add(key)
        return False

    def _fetch_and_persist_organization(
        self,
        owner_login: str,
        owner_data: Dict[str, Any],
        normalized_login: str,
        session: Optional[Session] = None,
    ) -> Dict[str, Any]:
        """Hole Organisationsdaten von der API, persistiäre sie und aktualisiere Caches.

        Gibt erweiterte Organisationsdaten zurück oder fällt auf ``owner_data`` zurück.
        """

        try:
            org_details = self.api.get_organization(owner_login)
            if org_details:
                self._org_cache[owner_login] = org_details
                logger.debug(
                    f"Erweiterte Organisationsdaten für {owner_login} abgerufen und gecacht"
                )

                # Füge die Organisation in die Datenbank ein oder aktualisiere sie
                self.db.insert_organization(org_details, session=session)

                # Erfasse neue Organisation für Performance-Tracking
                if self.performance_tracker:
                    self.performance_tracker.record_new_owner("organizations")

                # Füge ID zu bekannten IDs hinzu
                oid = org_details.get("id")
                if oid is not None:
                    self._known_owner_ids.add(oid)

                return org_details
            # API hat keine Daten zurückgegeben (z.B. 404, gelöschte Org)
            logger.debug(f"Keine Daten für Organisation {owner_login} (möglicherweise gelöscht)")
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"Fehler beim Abrufen erweiterter Organisationsdaten für {owner_login}: {e}"
            )
        finally:
            # IMMER als bekannt markieren, um wiederholte API-Calls zu vermeiden
            self._known_owners.add(owner_login)

        # Fallback: Verwende die Basis-Daten, wenn keine erweiterten Daten verfügbar sind
        return owner_data

    def _fetch_and_persist_contributor(
        self,
        owner_login: str,
        normalized_login: str,
        session: Optional[Session] = None,
    ) -> None:
        """Hole Contributor-Daten von der API, persistiäre sie und aktualisiere Caches."""

        try:
            contributor_data = self.api.get_contributor(owner_login)
            if contributor_data:
                # Speichere im Cache
                self._contributor_cache[owner_login] = contributor_data
                # Füge in die Datenbank ein oder aktualisiere
                self.db.insert_contributor(contributor_data, session=session)
                logger.debug(
                    f"Erweiterte Contributor-Metadaten für {owner_login} abgerufen und gecacht"
                )

                # Erfasse neuen Contributor für Performance-Tracking
                if self.performance_tracker:
                    self.performance_tracker.record_new_owner("contributors")

                # Füge ID zu bekannten IDs hinzu
                cid = contributor_data.get("id")
                if cid is not None:
                    self._known_owner_ids.add(cid)
            else:
                # API hat keine Daten zurückgegeben (z.B. 404, gelöschter User)
                logger.debug(f"Keine Daten für Contributor {owner_login} (möglicherweise gelöscht)")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Fehler beim Abrufen von Contributor-Metadaten für {owner_login}: {e}")
        finally:
            # IMMER als bekannt markieren, um wiederholte API-Calls zu vermeiden
            # (auch bei Fehlern oder gelöschten Usern)
            self._known_owners.add(normalized_login)

    def process_organization(
        self,
        owner_login: str,
        owner_data: Dict[str, Any],
        session: Optional[Session] = None,
    ) -> Dict[str, Any]:
        """
        Verarbeitet Organisationsdaten.

        Args:
            owner_login: Login-Name der Organisation
            owner_data: Basis-Daten der Organisation

        Returns:
            Erweiterte Organisationsdaten
        """
        normalized_login = owner_login.lower()

        # Prüfe, ob die Organisation bereits im aktuellen Batch verarbeitet wurde
        if self._is_owner_in_batch(
            normalized_login=normalized_login,
            owner_login=owner_login,
            store_normalized=True,
            owner_type_label="Organisation",
        ):
            return owner_data

        # Prüfe, ob die Organisation bereits in den bekannten Ownern ist (vorgeladene Datenbank)
        if normalized_login in self._known_owners:
            if self.performance_tracker:
                self.performance_tracker.record_known_owner_requested()
            logger.debug(f"Organisation {owner_login} ist bereits als bekannter Owner vorgeladen")
            # Prüfe, ob die Organisation bereits im Cache ist
            if owner_login in self._org_cache:
                org_details = self._org_cache[owner_login]
                logger.debug(f"Organisationsdaten für {owner_login} aus Cache verwendet")
                return org_details
            # Wenn nicht im Cache, aber bekannt, verwende die Basis-Daten
            return owner_data

        # Prüfe, ob die Organisation bereits in der Datenbank existiert (Fallback, falls nicht vorgeladen)
        org_exists = self.db.check_organization_exists(owner_login)
        if org_exists:
            if self.performance_tracker:
                self.performance_tracker.record_known_owner_requested()
            logger.debug(f"Organisation {owner_login} existiert bereits in der Datenbank")
            # Für zukünftige Abfragen merken (lowercase)
            self._known_owners.add(normalized_login)
            return owner_data

        # Prüfe, ob die Organisation bereits im Cache ist
        if owner_login in self._org_cache:
            org_details = self._org_cache[owner_login]
            logger.debug(f"Organisationsdaten für {owner_login} aus Cache verwendet")
            return org_details

        return self._fetch_and_persist_organization(
            owner_login=owner_login,
            owner_data=owner_data,
            normalized_login=normalized_login,
            session=session,
        )

    def process_contributor(
        self,
        owner_login: str,
        session: Optional[Session] = None,
    ) -> None:
        """
        Verarbeitet Contributor-Metadaten.

        Args:
            owner_login: Login-Name des Contributors
        """
        normalized_login = owner_login.lower()

        # Prüfe, ob der Contributor bereits im aktuellen Batch verarbeitet wurde
        if self._is_owner_in_batch(
            normalized_login=normalized_login,
            owner_login=owner_login,
            store_normalized=False,
            owner_type_label="Contributor",
        ):
            return

        # Prüfe, ob der Contributor bereits in den bekannten Ownern ist (vorgeladene Datenbank)
        if normalized_login in self._known_owners:
            if self.performance_tracker:
                self.performance_tracker.record_known_owner_requested()
            logger.debug(f"Contributor {owner_login} ist bereits als bekannter Owner vorgeladen")
            return

        # Prüfe, ob der Contributor bereits in der Datenbank existiert (Fallback, falls nicht vorgeladen)
        contributor_exists = self.db.check_contributor_exists(owner_login)
        if contributor_exists:
            if self.performance_tracker:
                self.performance_tracker.record_known_owner_requested()
            logger.debug(f"Contributor {owner_login} existiert bereits in der Datenbank")
            # Für zukünftige Abfragen merken (lowercase)
            self._known_owners.add(normalized_login)
            return

        # Prüfe, ob der Contributor bereits im Cache ist
        if owner_login in self._contributor_cache:
            logger.debug(f"Contributor-Daten für {owner_login} aus Cache verwendet")
            return

        self._fetch_and_persist_contributor(
            owner_login=owner_login,
            normalized_login=normalized_login,
            session=session,
        )

    def process_contributors_batch(
        self,
        contributor_logins: List[str],
        contributor_ids: Optional[List[Optional[int]]] = None,
        batch_id: Optional[str] = None,
    ) -> None:
        """
        Verarbeitet mehrere Contributors in einem Batch.

        Args:
            contributor_logins: Liste der GitHub-Benutzernamen der Contributors
            contributor_ids: Optionale Liste der GitHub-IDs (parallel zu logins)
        """
        if not contributor_logins:
            return

        unknown_logins, known_count, total = self._identify_new_contributors(
            contributor_logins, contributor_ids
        )
        new_count = len(unknown_logins)
        prefix = f"[{batch_id}] " if batch_id else ""
        logger.info(
            f"{prefix}Owners (Base-Data): {total} checked ({new_count} new API calls, {known_count} known)"
        )

        if known_count > 0 and self.performance_tracker:
            for _ in range(known_count):
                self.performance_tracker.record_known_owner_requested()

        # Verarbeite die unbekannten Contributors
        if unknown_logins:
            self._process_unknown_contributors(unknown_logins)

    def _identify_new_contributors(
        self,
        contributor_logins: List[str],
        contributor_ids: Optional[List[Optional[int]]] = None,
    ) -> Tuple[List[str], int, int]:
        """Identifiziere unbekannte Contributor-Logins anhand von Login und optionaler ID."""

        # Entferne Duplikate und baue Login-zu-ID-Mapping (mit Normalisierung)
        login_to_id: Dict[str, Optional[int]] = {}
        for i, raw_login in enumerate(contributor_logins):
            if not raw_login:
                continue
            login = raw_login.lower()
            if login not in login_to_id:
                cid = contributor_ids[i] if contributor_ids and i < len(contributor_ids) else None
                login_to_id[login] = cid

        unique_logins = list(login_to_id.keys())
        total = len(contributor_logins)

        # Filtere bereits bekannte Owners (nach Login ODER ID)
        unknown_logins: List[str] = []
        known_count = 0
        for login in unique_logins:
            cid = login_to_id.get(login)
            is_known = login in self._known_owners or (
                cid is not None and cid in self._known_owner_ids
            )
            if is_known:
                known_count += 1
            else:
                unknown_logins.append(login)

        return unknown_logins, known_count, total

    def _process_unknown_contributors(self, unknown_logins: List[str]) -> None:
        """Verarbeite alle unbekannten Contributors mit Rate-Limit-Überwachung."""

        # Überwache das Rate-Limit vor dem Batch
        rate_limit_info = self.api.monitor_rate_limit(threshold_percent=15)
        if rate_limit_info.get("has_critical", False):
            logger.warning("Kritisches Rate-Limit vor Contributor-Batch-Verarbeitung erkannt")

        # Verarbeite jeden unbekannten Contributor
        for login in unknown_logins:
            self.process_contributor(login)

        # Überwache das Rate-Limit nach dem Batch
        rate_limit_info = self.api.monitor_rate_limit(threshold_percent=10)
        if rate_limit_info.get("has_warnings", False):
            for warning in rate_limit_info.get("warnings", []):
                logger.warning(warning)

    def process_organizations_batch(
        self,
        organizations: List[Dict[str, Any]],
        batch_id: Optional[str] = None,
    ) -> None:
        """Verarbeitet mehrere Organisationen in einem Batch.

        Diese Methode arbeitet auf Basis der bereits extrahierten Organisations-Basisdaten
        aus den Repositories.
        """
        if not organizations:
            return

        # Duplikate nach Login entfernen
        login_to_org: Dict[str, Dict[str, Any]] = {}
        for org in organizations:
            login = org.get("login")
            if not login:
                continue
            normalized_login = login.lower()
            if normalized_login not in login_to_org:
                login_to_org[normalized_login] = org

        unique_logins = list(login_to_org.keys())
        total = len(unique_logins)

        known_count = 0
        unknown_logins: List[str] = []
        for login in unique_logins:
            # Nur nach Login, IDs werden über _known_owner_ids bereits abgedeckt,
            # wenn process_organization sie setzt.
            if login in self._known_owners:
                known_count += 1
            else:
                unknown_logins.append(login)

        new_count = len(unknown_logins)
        prefix = f"[{batch_id}] " if batch_id else ""
        logger.info(
            f"{prefix}Organizations (Details): {total} checked ({new_count} new API calls, {known_count} known)"
        )

        # Verarbeite nur die unbekannten Organisationen im Detail
        if unknown_logins:
            for login in unknown_logins:
                org_data = login_to_org[login]
                self.process_organization(login, org_data)

    def process_repository_owner(
        self,
        repo_data: Dict[str, Any],
        session: Optional[Session] = None,
    ) -> Dict[str, Any]:
        """
        Verarbeitet den Owner eines Repositories.

        Args:
            repo_data: Repository-Daten aus der GitHub API

        Returns:
            Aktualisierte Repository-Daten mit erweiterten Owner-Informationen
        """
        # Prüfe, ob das Repository einen Owner hat
        owner_data = repo_data.get("owner", {})
        owner_login = owner_data.get("login") if owner_data else None
        owner_type = owner_data.get("type") if owner_data else None

        if not owner_login or not owner_type:
            logger.warning(
                f"Repository {repo_data.get('full_name')} hat keine gültigen Owner-Daten"
            )
            return repo_data

        # Verarbeite Organisationsdaten, wenn der Owner eine Organisation ist
        if owner_type == "Organization":
            if "organization" not in repo_data or not repo_data["organization"]:
                # Setze die Organisationsdaten basierend auf dem Eigentümer
                repo_data["organization"] = owner_data
                logger.debug(
                    f"Organisationsdaten aus Owner-Feld für {repo_data.get('full_name')} hinzugefügt"
                )

                # Hole erweiterte Organisationsdaten und aktualisiere das Repository direkt
                repo_data["organization"] = self.process_organization(
                    owner_login,
                    owner_data,
                    session=session,
                )

        # Verarbeite Contributor-Metadaten, wenn der Owner ein User ist
        elif owner_type == "User":
            self.process_contributor(owner_login, session=session)

        return repo_data
