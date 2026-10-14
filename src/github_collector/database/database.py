"""Datenbankoperationen für GitHub-Daten."""

import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple, TypeVar, Union

from sqlalchemy import and_, create_engine, desc, exists, func, or_
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from github_collector.config import DB_CONFIG, DB_URL
from github_collector.monitoring import record_db_operation
from github_collector.utils.retry import retry_with_backoff

from .models import (
    Base,
    Contributor,
    OrbisEnrichment,
    Organization,
    Repository,
    RepositoryContributor,
)

# Zentrale Model-Registry zur generischen Nutzung in Hilfsmethoden (Export, Geocoding, etc.)
_MODEL_REGISTRY: Dict[str, Base] = {
    "repositories": Repository,
    "contributors": Contributor,
    "organizations": Organization,
}

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _create_engine(database_url: str) -> Engine:
    """Erzeuge eine SQLAlchemy Engine mit sinnvollem Connection-Pooling.

    Für produktive Datenbanken (z.B. MySQL) wird explizit QueuePool mit
    Einstellungen aus DB_CONFIG verwendet. SQLite wird separat behandelt.
    """

    # SQLite: eigene Behandlung, typischerweise ohne QueuePool
    if database_url.startswith("sqlite://"):
        connect_args: Dict[str, Union[bool, str]] = {}
        if database_url.startswith("sqlite:///"):
            # Mehr-Thread-Zugriffe auf Datei-basiertes SQLite erlauben
            connect_args["check_same_thread"] = False

        return create_engine(database_url, connect_args=connect_args)

    # Standardfall: z. B. MySQL
    return create_engine(
        database_url,
        poolclass=QueuePool,
        pool_size=DB_CONFIG["pool_size"],
        pool_timeout=DB_CONFIG["pool_timeout"],
        pool_recycle=DB_CONFIG["pool_recycle"],
        pool_pre_ping=True,
    )


_engine: Engine = _create_engine(DB_URL)
SessionLocal: sessionmaker[Session] = sessionmaker(bind=_engine, expire_on_commit=False)


@contextmanager
def session_scope(session_factory: Optional[sessionmaker[Session]] = None):
    """Context-Manager für eine Datenbank-Session mit Commit/Rollback.

    Args:
        session_factory: Optionale eigene Session-Fabrik (z. B. aus init_db),
            standardmässig wird die globale SessionLocal verwendet.
    """

    factory = session_factory or SessionLocal
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(database_url: str, reset_db: bool = False) -> sessionmaker[Session]:
    """Datenbank initialisieren.

    Args:
        database_url: Datenbank-URL
        reset_db: Wenn True, Datenbank zurücksetzen (für SQLite)
    """
    # Für SQLite-Datenbanken: Prüfen, ob die Datei existiert und löschen, wenn reset_db True ist
    if reset_db and database_url.startswith("sqlite:///"):
        db_path = database_url.replace("sqlite:///", "")
        if os.path.exists(db_path):
            try:
                logger.info(f"Entferne bestehende Datenbankdatei: {db_path}")
                os.remove(db_path)
                logger.info("Bestehende Datenbankdatei erfolgreich entfernt")
            except Exception as e:
                logger.error(f"Fehler beim Entfernen der Datenbankdatei: {e}")

    # Engine erstellen und Tabellen erstellen
    engine = _create_engine(database_url)
    logger.info("Erstelle Datenbanktabellen, falls sie nicht existieren...")
    Base.metadata.create_all(engine)

    # Session-Factory zurückgeben (Abwärtskompatibilität)
    # expire_on_commit=False, damit Objekte nach Commit weiterhin nutzbar bleiben,
    # obwohl die Session im session_scope geschlossen wird.
    return sessionmaker(bind=engine, expire_on_commit=False)


def _missing_country_filter(model, include_marked_empty: bool):
    """Filter für Einträge ohne Country Code, wahlweise einschließlich der mit "" markierten."""
    if include_marked_empty:
        return or_(model.country_code.is_(None), model.country_code == "")
    return model.country_code.is_(None)


class GitHubDatabase:
    """
    Hauptschnittstelle für Datenbankoperationen.

    Diese Klasse bietet Methoden zum Einfügen, Abfragen und Aktualisieren
    von GitHub-Daten in der Datenbank.
    """

    def __init__(self, db_path: str = None):
        """Datenbankverbindung initialisieren.

        Args:
            db_path: Vollständige SQLAlchemy-URL
                (z.B. mysql+pymysql://...) oder None für In-Memory-DB
        """
        if db_path:
            self.db_url = db_path  # Keine Manipulation, akzeptiere jede SQLAlchemy-URL
        else:
            self.db_url = "sqlite:///:memory:"
        # Session-Factory pro Instanz; einzelne Operationen nutzen kurze Sessions
        self.session_factory = init_db(self.db_url)
        logger.info(f"Datenbankverbindung initialisiert: {self.db_url}")

    @retry_with_backoff(
        max_attempts=3,
        backoff_factor=2.0,
        initial_delay=0.5,
        exceptions=OperationalError,
    )
    def _run_in_session(
        self,
        operation: Callable[[Session], T],
        session: Optional[Session] = None,
    ) -> T:
        """Hilfsfunktion, um Operationen mit optionaler externer Session auszuführen.

        Wenn eine Session übergeben wird, wird diese verwendet, ohne Commit/Rollback
        zu steuern. Andernfalls wird ein eigener session_scope mit der lokalen
        Session-Factory geöffnet.
        """

        if session is not None:
            return operation(session)

        with session_scope(self.session_factory) as scoped_session:
            return operation(scoped_session)

    def insert_repository(
        self,
        repo_data: dict,
        session: Optional[Session] = None,
    ) -> Repository:
        """
        Repository in die Datenbank einfügen.

        Args:
            repo_data: Repository-Daten von der GitHub API

        Returns:
            Repository-Objekt
        """

        def _operation(sess: Session) -> Repository:
            # Prüfen, ob Repository bereits existiert
            existing = sess.query(Repository).filter_by(id=repo_data["id"]).first()
            if existing:
                logger.debug(
                    f"Repository {repo_data['full_name']} existiert bereits, wird aktualisiert"
                )
                return self.update_repository(existing, repo_data, session=sess)

            # Eigentümer einfügen oder abrufen
            owner_data = repo_data.get("owner", {})
            if owner_data:
                owner = self.insert_contributor(owner_data, session=sess)
            else:
                logger.warning(f"Repository {repo_data['full_name']} hat keinen Eigentümer")
                return None

            # Organisation einfügen oder abrufen, falls vorhanden
            organization = None
            if "organization" in repo_data and repo_data["organization"]:
                organization = self.insert_organization(repo_data["organization"], session=sess)

            # Repository erstellen
            repo = Repository(
                id=repo_data["id"],
                name=repo_data["name"],
                full_name=repo_data["full_name"],
                owner_id=owner.id,
                organization_id=organization.id if organization else None,
                description=repo_data.get("description"),
                homepage=repo_data.get("homepage"),
                language=repo_data.get("language"),
                private=repo_data.get("private", False),
                fork=repo_data.get("fork", False),
                default_branch=repo_data.get("default_branch"),
                size=repo_data.get("size"),
                stargazers_count=repo_data.get("stargazers_count", 0),
                watchers_count=repo_data.get("watchers_count", 0),
                forks_count=repo_data.get("forks_count", 0),
                open_issues_count=repo_data.get("open_issues_count", 0),
                contributors_count=repo_data.get("contributors_count"),
                commits_count=repo_data.get("commits_count"),
                pull_requests_count=repo_data.get("pull_requests_count"),
                created_at=self._parse_datetime(repo_data.get("created_at")),
                updated_at=self._parse_datetime(repo_data.get("updated_at")),
                pushed_at=self._parse_datetime(repo_data.get("pushed_at")),
            )

            # Repository in die Datenbank einfügen
            sess.add(repo)
            logger.info(f"Repository {repo.full_name} in die Datenbank eingefügt")

            return repo

        return self._run_in_session(_operation, session=session)

    def update_repository(
        self,
        repo: Repository,
        repo_data: dict,
        session: Optional[Session] = None,
    ) -> Repository:
        """
        Repository in der Datenbank aktualisieren.

        Args:
            repo: Bestehendes Repository-Objekt
            repo_data: Neue Repository-Daten

        Returns:
            Aktualisiertes Repository-Objekt
        """

        def _operation(sess: Session) -> Repository:
            # Felder aktualisieren
            repo.name = repo_data.get("name", repo.name)
            repo.full_name = repo_data.get("full_name", repo.full_name)
            repo.description = repo_data.get("description", repo.description)
            repo.homepage = repo_data.get("homepage", repo.homepage)
            repo.language = repo_data.get("language", repo.language)
            repo.private = repo_data.get("private", repo.private)
            repo.fork = repo_data.get("fork", repo.fork)
            repo.default_branch = repo_data.get("default_branch", repo.default_branch)
            repo.size = repo_data.get("size", repo.size)
            repo.stargazers_count = repo_data.get("stargazers_count", repo.stargazers_count)
            repo.watchers_count = repo_data.get("watchers_count", repo.watchers_count)
            repo.forks_count = repo_data.get("forks_count", repo.forks_count)
            repo.open_issues_count = repo_data.get("open_issues_count", repo.open_issues_count)
            repo.contributors_count = repo_data.get("contributors_count", repo.contributors_count)
            repo.commits_count = repo_data.get("commits_count", repo.commits_count)
            repo.pull_requests_count = repo_data.get(
                "pull_requests_count", repo.pull_requests_count
            )
            repo.updated_at = self._parse_datetime(repo_data.get("updated_at", repo.updated_at))
            repo.pushed_at = self._parse_datetime(repo_data.get("pushed_at", repo.pushed_at))

            # Collection-Metadaten aktualisieren (Phase 2.1.7)
            # Das Tupel (collected_at, stars_at_collection) muss konsistent sein
            repo.collected_at = datetime.now(timezone.utc)
            repo.stars_at_collection = repo_data.get("stargazers_count", repo.stargazers_count)

            # Organisation aktualisieren, falls vorhanden
            if "organization" in repo_data and repo_data["organization"]:
                organization = self.insert_organization(repo_data["organization"], session=sess)
                repo.organization_id = organization.id

            logger.debug(f"Repository {repo.full_name} aktualisiert")
            return repo

        return self._run_in_session(_operation, session=session)

    def check_contributor_exists(
        self,
        login: str,
        session: Optional[Session] = None,
    ) -> bool:
        """
        Prüft, ob ein Contributor mit dem angegebenen Login bereits in der Datenbank existiert.

        Args:
            login: GitHub-Benutzername des Contributors

        Returns:
            True, wenn der Contributor existiert, sonst False
        """

        def _operation(sess: Session) -> bool:
            return sess.query(Contributor).filter_by(login=login).first() is not None

        return self._run_in_session(_operation, session=session)

    def check_organization_exists(
        self,
        login: str,
        session: Optional[Session] = None,
    ) -> bool:
        """
        Prüft, ob eine Organisation mit dem angegebenen Login bereits in der Datenbank existiert.

        Args:
            login: GitHub-Benutzername der Organisation

        Returns:
            True, wenn die Organisation existiert, sonst False
        """

        def _operation(sess: Session) -> bool:
            return sess.query(Organization).filter_by(login=login).first() is not None

        return self._run_in_session(_operation, session=session)

    def check_repository_exists(
        self,
        repo_id: int,
        session: Optional[Session] = None,
    ) -> bool:
        """
        Prüft, ob ein Repository mit der angegebenen ID bereits in der Datenbank existiert.

        Args:
            repo_id: GitHub-Repository-ID

        Returns:
            True, wenn das Repository existiert, sonst False
        """

        def _operation(sess: Session) -> bool:
            return sess.query(Repository).filter_by(id=repo_id).first() is not None

        return self._run_in_session(_operation, session=session)

    def get_all_contributor_logins(
        self,
        session: Optional[Session] = None,
    ) -> List[str]:
        """
        Ruft alle Contributor-Logins aus der Datenbank ab.

        Returns:
            Liste aller Contributor-Logins
        """

        def _operation(sess: Session) -> List[str]:
            try:
                # Optimierte Abfrage, die nur die Login-Spalte abruft
                logins = [row[0] for row in sess.query(Contributor.login).all()]
                return logins
            except Exception as e:
                logger.error(f"Fehler beim Abrufen aller Contributor-Logins: {e}")
                return []

        return self._run_in_session(_operation, session=session)

    def get_all_contributor_ids(
        self,
        session: Optional[Session] = None,
    ) -> List[int]:
        """
        Ruft alle Contributor-IDs aus der Datenbank ab.

        Returns:
            Liste aller Contributor-IDs
        """

        def _operation(sess: Session) -> List[int]:
            try:
                ids = [row[0] for row in sess.query(Contributor.id).all()]
                return ids
            except Exception as e:
                logger.error(f"Fehler beim Abrufen aller Contributor-IDs: {e}")
                return []

        return self._run_in_session(_operation, session=session)

    def get_all_organization_ids(
        self,
        session: Optional[Session] = None,
    ) -> List[int]:
        """
        Ruft alle Organizations-IDs aus der Datenbank ab.

        Returns:
            Liste aller Organizations-IDs
        """

        def _operation(sess: Session) -> List[int]:
            try:
                ids = [row[0] for row in sess.query(Organization.id).all()]
                return ids
            except Exception as e:
                logger.error(f"Fehler beim Abrufen aller Organization-IDs: {e}")
                return []

        return self._run_in_session(_operation, session=session)

    def get_contributors(
        self,
        limit: Optional[int] = None,
        session: Optional[Session] = None,
    ) -> List[Contributor]:
        """
        Gibt alle Contributor-Objekte aus der Datenbank zurück.
        Optional kann ein Limit angegeben werden.

        Args:
            limit: Maximale Anzahl der zurückgegebenen Contributors (optional)
        Returns:
            Liste von Contributor-Objekten
        """

        def _operation(sess: Session) -> List[Contributor]:
            try:
                query = sess.query(Contributor)
                if limit is not None:
                    query = query.limit(limit)
                contributors = query.all()
                return contributors
            except Exception as e:
                logger.error(f"Fehler beim Abrufen der Contributors: {e}")
                return []

        return self._run_in_session(_operation, session=session)

    def get_contributors_without_country_code(
        self,
        limit: Optional[int] = None,
        session: Optional[Session] = None,
    ) -> List[Contributor]:
        """
        Gibt alle Contributor-Objekte zurück, deren country_code nicht gesetzt ist.
        Optional kann ein Limit angegeben werden.

        Args:
            limit: Maximale Anzahl der zurückgegebenen Contributors (optional)
        Returns:
            Liste von Contributor-Objekten ohne country_code
        """

        def _operation(sess: Session) -> List[Contributor]:
            try:
                query = sess.query(Contributor).filter(Contributor.country_code.is_(None))
                if limit is not None:
                    query = query.limit(limit)
                contributors = query.all()
                return contributors
            except Exception as e:
                logger.error(f"Fehler beim Abrufen der Contributors ohne Ländercode: {e}")
                return []

        return self._run_in_session(_operation, session=session)

    def get_organizations_without_country_code(
        self,
        limit: Optional[int] = None,
        session: Optional[Session] = None,
    ) -> List[Organization]:
        """
        Gibt alle Organization-Objekte zurück, deren country_code nicht gesetzt ist.
        Optional kann ein Limit angegeben werden.

        Args:
            limit: Maximale Anzahl der zurückgegebenen Organisationen (optional)
        Returns:
            Liste von Organization-Objekten ohne country_code
        """

        def _operation(sess: Session) -> List[Organization]:
            try:
                query = sess.query(Organization).filter(Organization.country_code.is_(None))
                if limit is not None:
                    query = query.limit(limit)
                organizations = query.all()
                return organizations
            except Exception as e:
                logger.error(f"Fehler beim Abrufen der Organisationen ohne Ländercode: {e}")
                return []

        return self._run_in_session(_operation, session=session)

    def get_all_organization_logins(
        self,
        session: Optional[Session] = None,
    ) -> List[str]:
        """
        Ruft alle Organization-Logins aus der Datenbank ab.

        Returns:
            Liste aller Organization-Logins
        """

        def _operation(sess: Session) -> List[str]:
            try:
                # Optimierte Abfrage, die nur die Login-Spalte abruft
                logins = [row[0] for row in sess.query(Organization.login).all()]
                return logins
            except Exception as e:
                logger.error(f"Fehler beim Abrufen aller Organization-Logins: {e}")
                return []

        return self._run_in_session(_operation, session=session)

    def insert_contributor(
        self,
        contributor_data: dict,
        session: Optional[Session] = None,
    ) -> Contributor:
        """
        Contributor in die Datenbank einfügen oder aktualisieren.

        Args:
            contributor_data: Contributor-Daten von der GitHub API

        Returns:
            Contributor-Objekt
        """

        def _operation(sess: Session) -> Contributor:
            # Prüfen, ob Contributor bereits existiert
            existing = sess.query(Contributor).filter_by(id=contributor_data["id"]).first()
            if existing:
                return self.update_contributor(existing, contributor_data, session=sess)

            # Neuen Contributor erstellen
            contributor = Contributor(
                id=contributor_data["id"],
                login=contributor_data["login"],
                name=contributor_data.get("name"),
                email=contributor_data.get("email"),
                type=contributor_data.get("type"),
                avatar_url=contributor_data.get("avatar_url"),
                company=contributor_data.get("company"),
                blog=contributor_data.get("blog"),
                location=contributor_data.get("location"),
                country_code=contributor_data.get("country_code"),
                region=contributor_data.get("region"),
                bio=contributor_data.get("bio"),
                twitter_username=contributor_data.get("twitter_username"),
                public_repos=contributor_data.get("public_repos"),
                public_gists=contributor_data.get("public_gists"),
                followers=contributor_data.get("followers"),
                following=contributor_data.get("following"),
                created_at=self._parse_datetime(contributor_data.get("created_at")),
                updated_at=self._parse_datetime(contributor_data.get("updated_at")),
            )

            # Contributor in die Datenbank einfügen
            sess.add(contributor)
            logger.debug(f"Contributor {contributor.login} in die Datenbank eingefügt")

            return contributor

        return self._run_in_session(_operation, session=session)

    def update_contributor(
        self,
        contributor: Contributor,
        contributor_data: dict,
        session: Optional[Session] = None,
    ) -> Contributor:
        """
        Contributor in der Datenbank aktualisieren.

        Args:
            contributor: Bestehendes Contributor-Objekt
            contributor_data: Neue Contributor-Daten

        Returns:
            Aktualisiertes Contributor-Objekt
        """

        def _operation(sess: Session) -> Contributor:
            # Stelle sicher, dass der Contributor an die aktuelle Session gebunden ist
            managed_contributor = sess.merge(contributor)

            # Felder aktualisieren
            managed_contributor.login = contributor_data.get("login", managed_contributor.login)
            managed_contributor.name = contributor_data.get("name", managed_contributor.name)
            managed_contributor.email = contributor_data.get("email", managed_contributor.email)
            managed_contributor.type = contributor_data.get("type", managed_contributor.type)
            managed_contributor.avatar_url = contributor_data.get(
                "avatar_url", managed_contributor.avatar_url
            )
            managed_contributor.company = contributor_data.get(
                "company", managed_contributor.company
            )
            managed_contributor.blog = contributor_data.get("blog", managed_contributor.blog)
            managed_contributor.location = contributor_data.get(
                "location", managed_contributor.location
            )
            managed_contributor.country_code = contributor_data.get(
                "country_code", managed_contributor.country_code
            )
            managed_contributor.region = contributor_data.get("region", managed_contributor.region)
            managed_contributor.bio = contributor_data.get("bio", managed_contributor.bio)
            managed_contributor.twitter_username = contributor_data.get(
                "twitter_username", managed_contributor.twitter_username
            )
            managed_contributor.public_repos = contributor_data.get(
                "public_repos", managed_contributor.public_repos
            )
            managed_contributor.public_gists = contributor_data.get(
                "public_gists", managed_contributor.public_gists
            )
            managed_contributor.followers = contributor_data.get(
                "followers", managed_contributor.followers
            )
            managed_contributor.following = contributor_data.get(
                "following", managed_contributor.following
            )
            managed_contributor.updated_at = self._parse_datetime(
                contributor_data.get("updated_at", managed_contributor.updated_at)
            )

            logger.debug(f"Contributor {managed_contributor.login} aktualisiert")
            return managed_contributor

        return self._run_in_session(_operation, session=session)

    def insert_organization(
        self,
        org_data: dict,
        session: Optional[Session] = None,
    ) -> Organization:
        """
        Organisation in die Datenbank einfügen oder aktualisieren.

        Args:
            org_data: Organisations-Daten von der GitHub API

        Returns:
            Organisation-Objekt
        """

        def _operation(sess: Session) -> Organization:
            # Prüfen, ob Organisation bereits existiert
            existing = sess.query(Organization).filter_by(id=org_data["id"]).first()
            if existing:
                return self.update_organization(existing, org_data, session=sess)

            # Neue Organisation erstellen
            organization = Organization(
                id=org_data["id"],
                login=org_data["login"],
                name=org_data.get("name"),
                email=org_data.get("email"),
                type=org_data.get("type"),
                avatar_url=org_data.get("avatar_url"),
                company=org_data.get("company"),
                blog=org_data.get("blog"),
                location=org_data.get("location"),
                country_code=org_data.get("country_code"),
                region=org_data.get("region"),
                bio=org_data.get("bio"),
                twitter_username=org_data.get("twitter_username"),
                public_repos=org_data.get("public_repos"),
                public_gists=org_data.get("public_gists"),
                followers=org_data.get("followers"),
                following=org_data.get("following"),
                public_members=org_data.get("public_members"),
                created_at=self._parse_datetime(org_data.get("created_at")),
                updated_at=self._parse_datetime(org_data.get("updated_at")),
            )

            # Organisation in die Datenbank einfügen
            sess.add(organization)
            logger.debug(f"Organisation {organization.login} in die Datenbank eingefügt")

            return organization

        return self._run_in_session(_operation, session=session)

    def update_organization(
        self,
        organization: Organization,
        org_data: dict,
        session: Optional[Session] = None,
    ) -> Organization:
        """
        Organisation in der Datenbank aktualisieren.

        Args:
            organization: Bestehendes Organisation-Objekt
            org_data: Neue Organisations-Daten

        Returns:
            Aktualisiertes Organisation-Objekt
        """

        def _operation(sess: Session) -> Organization:
            # Stelle sicher, dass die Organisation an die aktuelle Session gebunden ist
            managed_org = sess.merge(organization)

            # Felder aktualisieren
            managed_org.login = org_data.get("login", managed_org.login)
            managed_org.name = org_data.get("name", managed_org.name)
            managed_org.email = org_data.get("email", managed_org.email)
            managed_org.type = org_data.get("type", managed_org.type)
            managed_org.avatar_url = org_data.get("avatar_url", managed_org.avatar_url)
            managed_org.company = org_data.get("company", managed_org.company)
            managed_org.blog = org_data.get("blog", managed_org.blog)
            managed_org.location = org_data.get("location", managed_org.location)
            managed_org.country_code = org_data.get("country_code", managed_org.country_code)
            managed_org.region = org_data.get("region", managed_org.region)
            managed_org.bio = org_data.get("bio", managed_org.bio)
            managed_org.twitter_username = org_data.get(
                "twitter_username", managed_org.twitter_username
            )
            managed_org.public_repos = org_data.get("public_repos", managed_org.public_repos)
            managed_org.public_gists = org_data.get("public_gists", managed_org.public_gists)
            managed_org.followers = org_data.get("followers", managed_org.followers)
            managed_org.following = org_data.get("following", managed_org.following)
            managed_org.public_members = org_data.get("public_members", managed_org.public_members)
            managed_org.updated_at = self._parse_datetime(
                org_data.get("updated_at", managed_org.updated_at)
            )

            logger.debug(f"Organisation {managed_org.login} aktualisiert")
            return managed_org

        return self._run_in_session(_operation, session=session)

    # --- Orbis Enrichment (Hybrid-Ansatz) ---
    #
    # Setzt den in Orbis-Notizen (intern, ältere Entwürfe): enrichment/
    # orbis_matching_strategy_v2.md (Abschnitt 8) entworfenen, nie
    # implementierten "Hybrid-Ansatz" um: drei Ergebnis-Spalten direkt auf
    # `Organization` (bvd_id, country_code_orbis, orbis_enriched_at) für
    # schnelle Abfragen ohne JOIN, plus eine `OrbisEnrichment`-Zeile pro Lauf
    # für Historie/Rollback über `run_id`.

    def record_orbis_enrichment(
        self,
        organization_id: int,
        bvd_id: str,
        match_type: str,
        score: int,
        run_id: str,
        country_code: Optional[str] = None,
        company_name: Optional[str] = None,
        company_city: Optional[str] = None,
        company_website: Optional[str] = None,
        hq_method: Optional[str] = None,
        blog_matched: bool = False,
        enriched_at: Optional[datetime] = None,
        session: Optional[Session] = None,
    ) -> OrbisEnrichment:
        """Schreibe ein Orbis-Matching-Ergebnis für eine Organisation.

        Aktualisiert sowohl die drei Ergebnis-Spalten auf `Organization`
        (für schnelle Abfragen ohne JOIN) als auch die `OrbisEnrichment`-
        Lauf-Historie (für Auditierbarkeit und Rollback über `run_id`, siehe
        `scripts/migrations/add_orbis_enrichments_table.sql`).

        Der Firmen-Snapshot (`company_name`/`company_city`/`company_website`,
        `country_code` doppelt genutzt für `company_country`) wird DIREKT auf
        der Enrichment-Zeile mitgespeichert - siehe
        `scripts/migrations/consolidate_orbis_enrichments.sql`: eine frühere,
        separate Firmen-Cache-Tabelle erforderte für "welche GH-Org gehört zu
        diesem Match, wie heißt die Firma?" einen JOIN über zwei Tabellen.

        Args:
            organization_id: ID der zu aktualisierenden Organisation.
            bvd_id: Orbis BvD-Identifier des gewählten Kandidaten.
            match_type: Match-Type-Klassifikation (z.B. "combined_high").
            score: Matching-Score.
            run_id: UUID des Enrichment-Laufs (für Rollback).
            country_code: ISO-Ländercode aus Orbis, falls ermittelt.
            company_name: Firmenname (NAME_NATIVE) zum Zeitpunkt des Matches.
            company_city: Stadt (CITY_NATIVE), falls bekannt.
            company_website: Website, falls bekannt.
            hq_method: HQ-Kaskaden-Signal (z.B. "ROOT_DOMAIN_PRESENCE"), falls
                ein Multi-Hit über hq_detection.py aufgelöst wurde.
            blog_matched: Ob der Match über die Blog-Domain lief.
            enriched_at: Zeitstempel. Falls None, wird die aktuelle Zeit (UTC)
                verwendet.
            session: Optionale bestehende Session.

        Returns:
            Das neu erstellte OrbisEnrichment-Objekt.

        Raises:
            ValueError: Wenn keine Organisation mit `organization_id` existiert.
        """
        timestamp = enriched_at or datetime.now(timezone.utc)

        def _operation(sess: Session) -> OrbisEnrichment:
            organization = sess.query(Organization).filter_by(id=organization_id).first()
            if organization is None:
                raise ValueError(f"Keine Organisation mit ID {organization_id} gefunden")

            enrichment = OrbisEnrichment(
                organization_id=organization_id,
                bvd_id=bvd_id,
                company_name=company_name,
                company_country=country_code,
                company_city=company_city,
                company_website=company_website,
                match_type=match_type,
                hq_method=hq_method,
                score=score,
                blog_matched=blog_matched,
                run_id=run_id,
                enriched_at=timestamp,
                created_at=datetime.now(timezone.utc),
            )
            sess.add(enrichment)
            sess.flush()  # enrichment.id wird für current_orbis_enrichment_id benötigt

            organization.bvd_id = bvd_id
            organization.country_code_orbis = country_code
            organization.orbis_enriched_at = timestamp
            organization.current_orbis_enrichment_id = enrichment.id

            logger.debug(
                "Orbis-Enrichment für Organisation %s (ID %s): BVDID=%s, Score=%s, "
                "run_id=%s, enrichment_id=%s",
                organization.login,
                organization_id,
                bvd_id,
                score,
                run_id,
                enrichment.id,
            )
            return enrichment

        return self._run_in_session(_operation, session=session)

    def rollback_orbis_enrichment_run(
        self,
        run_id: str,
        session: Optional[Session] = None,
    ) -> int:
        """Macht einen kompletten Orbis-Enrichment-Lauf rückgängig.

        WICHTIG: Löscht die `OrbisEnrichment`-Zeilen für `run_id` NICHT mehr -
        setzt nur `reverted_at`. Ein Logbuch, das seine eigenen Einträge beim
        Rollback löscht, taugt nicht zur Nachprüfung (Fund beim Testen des
        echten Schreib-/Rollback-Pfads: danach gab es nirgendwo mehr eine
        Spur, welche GitHub-Organisation ursprünglich mit welcher Orbis-Firma
        verknüpft war). Setzt die drei Ergebnis-Spalten auf `Organization`
        zurück auf NULL (inkl. `current_orbis_enrichment_id`) - aber NUR für
        Organisationen, deren aktuellstes AKTIVES Enrichment aus diesem Lauf
        stammt (verhindert, dass ein späterer, andersartiger Lauf für
        dieselbe Organisation versehentlich mitzurückgerollt wird).

        Args:
            run_id: UUID des rückzurollenden Laufs.
            session: Optionale bestehende Session.

        Returns:
            Anzahl der zurückgerollten Organisationen.
        """

        def _operation(sess: Session) -> int:
            reverted_rows = (
                sess.query(OrbisEnrichment).filter_by(run_id=run_id, reverted_at=None).all()
            )

            if not reverted_rows:
                logger.info("Rollback run_id=%s: keine aktiven Enrichments gefunden", run_id)
                return 0

            affected_org_ids = [row.organization_id for row in reverted_rows]
            revert_timestamp = datetime.now(timezone.utc)
            for row in reverted_rows:
                row.reverted_at = revert_timestamp
            sess.flush()

            reset_count = 0
            organizations = (
                sess.query(Organization).filter(Organization.id.in_(affected_org_ids)).all()
            )
            for organization in organizations:
                # Nur zurücksetzen, wenn kein anderes, weiterhin AKTIVES
                # (nicht zurückgerolltes) Enrichment für dieselbe
                # Organisation übrig ist
                remaining = (
                    sess.query(OrbisEnrichment)
                    .filter_by(organization_id=organization.id, reverted_at=None)
                    .order_by(OrbisEnrichment.enriched_at.desc())
                    .first()
                )
                if remaining is not None:
                    organization.bvd_id = remaining.bvd_id
                    organization.country_code_orbis = remaining.company_country
                    organization.orbis_enriched_at = remaining.enriched_at
                    organization.current_orbis_enrichment_id = remaining.id
                else:
                    organization.bvd_id = None
                    organization.country_code_orbis = None
                    organization.orbis_enriched_at = None
                    organization.current_orbis_enrichment_id = None
                reset_count += 1

            logger.info(
                "Rollback run_id=%s: %d Organisationen zurückgesetzt, "
                "%d Logbuch-Zeilen als zurückgerollt markiert (nicht gelöscht)",
                run_id,
                reset_count,
                len(reverted_rows),
            )
            return reset_count

        return self._run_in_session(_operation, session=session)

    def bulk_upsert_contributors(  # noqa: C901
        self,
        contributors_data: List[dict],
        session: Optional[Session] = None,
        batch_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """Bulk-Upsert für Contributors basierend auf dem Login.

        Es werden zunächst alle bestehenden Contributors für die gegebenen Logins geladen.
        Neue Contributors werden als Objekte erstellt, per ``session.add_all`` hinzugefügt
        und via ``session.flush()`` persistent gemacht, sodass IDs verfügbar sind.

        Updates für bestehende Contributors werden aus Performance-Gründen vorerst
        nicht durchgeführt (First-Write-Wins-Ansatz).

        Args:
            contributors_data: Liste von Contributor-Dictionaries (GitHub API Daten).
            session: Optionale bestehende Session.

        Returns:
            Mapping von Login -> Contributor-ID.
        """

        def _operation(sess: Session) -> Dict[str, int]:  # noqa: C901
            if not contributors_data:
                return {}

            # Dedupliziere nach Login, wobei der erste Eintrag gewinnt
            unique_by_login: Dict[str, dict] = {}
            for data in contributors_data:
                login = data.get("login")
                if not login or login in unique_by_login:
                    continue
                unique_by_login[login] = data

            logins = list(unique_by_login.keys())
            if not logins:
                return {}

            # IDs einsammeln, falls vorhanden
            ids = [
                data.get("id") for data in unique_by_login.values() if data.get("id") is not None
            ]

            login_to_id: Dict[str, int] = {}

            # Bestehende Contributors anhand des Logins laden
            if logins:
                existing_rows_by_login = (
                    sess.query(Contributor.id, Contributor.login)
                    .filter(Contributor.login.in_(logins))
                    .all()
                )
                for id_, login in existing_rows_by_login:
                    login_to_id[login] = id_

            # Zusätzlich nach IDs suchen, um Fälle abzudecken, in denen sich der Login geändert hat
            existing_ids: Dict[int, str] = {}
            if ids:
                existing_rows_by_id = (
                    sess.query(Contributor.id, Contributor.login)
                    .filter(Contributor.id.in_(ids))
                    .all()
                )
                for id_, login in existing_rows_by_id:
                    existing_ids[id_] = login
                    # Stelle sicher, dass auch der bestehende Login im Mapping ist
                    if login:
                        login_to_id.setdefault(login, id_)

            # Bestimme neue Contributors (Logins/IDs, die noch nicht existieren)
            new_contributors: List[Contributor] = []
            for login, data in unique_by_login.items():
                data_id = data.get("id")

                # Wenn der Login bereits bekannt ist, verwende dessen ID
                if login in login_to_id:
                    continue

                # Wenn die ID bereits existiert, mappe den neuen Login auf diese ID
                if data_id is not None and data_id in existing_ids:
                    login_to_id[login] = data_id
                    continue

                contributor = Contributor(
                    id=data_id,
                    login=login,
                    name=data.get("name"),
                    email=data.get("email"),
                    type=data.get("type"),
                    avatar_url=data.get("avatar_url"),
                    company=data.get("company"),
                    blog=data.get("blog"),
                    location=data.get("location"),
                    country_code=data.get("country_code"),
                    region=data.get("region"),
                    bio=data.get("bio"),
                    twitter_username=data.get("twitter_username"),
                    public_repos=data.get("public_repos"),
                    public_gists=data.get("public_gists"),
                    followers=data.get("followers"),
                    following=data.get("following"),
                    created_at=self._parse_datetime(data.get("created_at")),
                    updated_at=self._parse_datetime(data.get("updated_at")),
                )
                new_contributors.append(contributor)

            if new_contributors:
                sess.add_all(new_contributors)
                # Flush, damit IDs (falls von der DB generiert) verfügbar sind
                sess.flush()

                for contributor in new_contributors:
                    if contributor.login:
                        login_to_id[contributor.login] = contributor.id

                # Phase 4.1: Prometheus-Metrik für DB-Operation
                record_db_operation(
                    table="contributors", operation="upsert", count=len(new_contributors)
                )

            # Batch-Logging
            prefix = f"[{batch_id}] " if batch_id else ""
            logger.info(
                "%sBulk-Upsert Contributors: %s neue Logins, %s bestehende Logins (insgesamt %s)",
                prefix,
                len(new_contributors),
                len(login_to_id) - len(new_contributors),
                len(login_to_id),
            )

            return login_to_id

        return self._run_in_session(_operation, session=session)

    def bulk_upsert_organizations(  # noqa: C901
        self,
        organizations_data: List[dict],
        session: Optional[Session] = None,
        batch_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """Bulk-Upsert für Organisationen basierend auf dem Login.

        Analog zu ``bulk_upsert_contributors`` werden bestehende Organisationen per
        Login geladen, neue Organisationen erstellt und eingefügt. Updates an
        bestehenden Einträgen werden aus Performance-Gründen vorerst ausgelassen.

        Args:
            organizations_data: Liste von Organisations-Dictionaries (GitHub API Daten).
            session: Optionale bestehende Session.

        Returns:
            Mapping von Login -> Organizations-ID.
        """

        def _operation(sess: Session) -> Dict[str, int]:  # noqa: C901
            if not organizations_data:
                return {}

            unique_by_login: Dict[str, dict] = {}
            for data in organizations_data:
                login = data.get("login")
                if not login or login in unique_by_login:
                    continue
                unique_by_login[login] = data

            logins = list(unique_by_login.keys())
            if not logins:
                return {}

            ids = [
                data.get("id") for data in unique_by_login.values() if data.get("id") is not None
            ]

            login_to_id: Dict[str, int] = {}

            # Bestehende Organisationen per Login laden
            if logins:
                existing_rows_by_login = (
                    sess.query(Organization.id, Organization.login)
                    .filter(Organization.login.in_(logins))
                    .all()
                )
                for id_, login in existing_rows_by_login:
                    login_to_id[login] = id_

            # Zusätzlich per ID suchen (Login-Änderungen)
            existing_ids: Dict[int, str] = {}
            if ids:
                existing_rows_by_id = (
                    sess.query(Organization.id, Organization.login)
                    .filter(Organization.id.in_(ids))
                    .all()
                )
                for id_, login in existing_rows_by_id:
                    existing_ids[id_] = login
                    if login:
                        login_to_id.setdefault(login, id_)

            new_organizations: List[Organization] = []
            for login, data in unique_by_login.items():
                data_id = data.get("id")

                if login in login_to_id:
                    continue

                if data_id is not None and data_id in existing_ids:
                    login_to_id[login] = data_id
                    continue

                organization = Organization(
                    id=data_id,
                    login=login,
                    name=data.get("name"),
                    email=data.get("email"),
                    type=data.get("type"),
                    avatar_url=data.get("avatar_url"),
                    company=data.get("company"),
                    blog=data.get("blog"),
                    location=data.get("location"),
                    country_code=data.get("country_code"),
                    region=data.get("region"),
                    bio=data.get("bio"),
                    twitter_username=data.get("twitter_username"),
                    public_repos=data.get("public_repos"),
                    public_gists=data.get("public_gists"),
                    followers=data.get("followers"),
                    following=data.get("following"),
                    public_members=data.get("public_members"),
                    created_at=self._parse_datetime(data.get("created_at")),
                    updated_at=self._parse_datetime(data.get("updated_at")),
                )
                new_organizations.append(organization)

            if new_organizations:
                sess.add_all(new_organizations)
                sess.flush()

                for organization in new_organizations:
                    if organization.login:
                        login_to_id[organization.login] = organization.id

                # Phase 4.1: Prometheus-Metrik für DB-Operation
                record_db_operation(
                    table="organizations", operation="upsert", count=len(new_organizations)
                )

            # Batch-Logging
            prefix = f"[{batch_id}] " if batch_id else ""
            logger.info(
                "%sBulk-Upsert Organizations: %s neue Logins, %s bestehende Logins (insgesamt %s)",
                prefix,
                len(new_organizations),
                len(login_to_id) - len(new_organizations),
                len(login_to_id),
            )

            return login_to_id

        return self._run_in_session(_operation, session=session)

    def bulk_upsert_repositories(  # noqa: C901
        self,
        repositories_data: List[dict],
        session: Optional[Session] = None,
        batch_id: Optional[str] = None,
    ) -> int:
        """Bulk-Upsert für Repositories.

        Für jedes Repository wird anhand der ID geprüft, ob es bereits existiert.
        Neue Repositories werden erzeugt und hinzugefügt, bestehende über die
        bestehende ``update_repository``-Logik aktualisiert.

        Diese Implementierung nutzt eine ORM-basierte Strategie als pragmatischen
        Ausgangspunkt. Eine spätere Optimierung auf SQLAlchemy Core mit
        ``INSERT ... ON DUPLICATE KEY UPDATE`` ist möglich, ohne die Signatur zu
        ändern.

        Args:
            repositories_data: Liste von Repository-Dictionaries, bereits
                normalisiert (inkl. owner_id / organization_id).
            session: Optionale bestehende Session.

        Returns:
            Anzahl der verarbeiteten Repositories.
        """

        def _operation(sess: Session) -> int:  # noqa: C901
            if not repositories_data:
                return 0

            # Dedupliziere nach Repository-ID, erste Sicht gewinnt
            unique_by_id: Dict[int, dict] = {}
            for data in repositories_data:
                repo_id = data.get("id")
                if repo_id is None or repo_id in unique_by_id:
                    continue
                unique_by_id[repo_id] = data

            ids = list(unique_by_id.keys())
            if not ids:
                return 0

            # Alle im Batch referenzierten Owner-IDs einsammeln
            owner_ids = {
                data.get("owner_id")
                for data in unique_by_id.values()
                if data.get("owner_id") is not None
            }

            existing_owner_ids: set[int] = set()
            if owner_ids:
                existing_owner_ids = {
                    id_
                    for (id_,) in sess.query(Contributor.id)
                    .filter(Contributor.id.in_(owner_ids))
                    .all()
                }

            # Alle im Batch referenzierten Organisations-IDs einsammeln
            organization_ids = {
                data.get("organization_id")
                for data in unique_by_id.values()
                if data.get("organization_id") is not None
            }

            existing_organization_ids: set[int] = set()
            if organization_ids:
                existing_organization_ids = {
                    id_
                    for (id_,) in sess.query(Organization.id)
                    .filter(Organization.id.in_(organization_ids))
                    .all()
                }

            # Bestehende Repositories laden
            existing_repos = sess.query(Repository).filter(Repository.id.in_(ids)).all()
            existing_by_id: Dict[int, Repository] = {repo.id: repo for repo in existing_repos}

            new_repositories: List[Repository] = []
            updated_count = 0
            skipped_missing_owner = 0

            for repo_id, data in unique_by_id.items():
                owner_id = data.get("owner_id")

                # Schutz: Repository nur einfügen/aktualisieren, wenn der Owner existiert.
                if owner_id is None or owner_id not in existing_owner_ids:
                    skipped_missing_owner += 1
                    logger.warning(
                        "Überspringe Repository %s (ID %s): Owner-ID %s nicht in contributors "
                        "vorhanden",
                        data.get("full_name"),
                        repo_id,
                        owner_id,
                    )
                    continue

                # Organisation-ID prüfen; falls unbekannt, auf None setzen,
                # um FK-Fehler zu vermeiden
                org_id = data.get("organization_id")
                if org_id is not None and org_id not in existing_organization_ids:
                    logger.warning(
                        "Setze Organization-ID auf NULL für Repository %s (ID %s): "
                        "Organization-ID %s nicht in organizations vorhanden",
                        data.get("full_name"),
                        repo_id,
                        org_id,
                    )
                    org_id = None
                else:
                    # bekannte Organisation oder None
                    pass

                existing = existing_by_id.get(repo_id)
                if existing is not None:
                    # Nutze die bestehende Update-Logik für Konsistenz
                    self.update_repository(existing, data, session=sess)
                    updated_count += 1
                    continue

                # Collection-Metadaten (Phase 2.1.7)
                now_utc = datetime.now(timezone.utc)
                stars_now = data.get("stargazers_count", 0)

                repo = Repository(
                    id=data["id"],
                    name=data["name"],
                    full_name=data["full_name"],
                    owner_id=owner_id,
                    organization_id=org_id,
                    description=data.get("description"),
                    homepage=data.get("homepage"),
                    language=data.get("language"),
                    private=data.get("private", False),
                    fork=data.get("fork", False),
                    default_branch=data.get("default_branch"),
                    size=data.get("size"),
                    stargazers_count=stars_now,
                    watchers_count=data.get("watchers_count", 0),
                    forks_count=data.get("forks_count", 0),
                    open_issues_count=data.get("open_issues_count", 0),
                    contributors_count=data.get("contributors_count"),
                    commits_count=data.get("commits_count"),
                    pull_requests_count=data.get("pull_requests_count"),
                    created_at=self._parse_datetime(data.get("created_at")),
                    updated_at=self._parse_datetime(data.get("updated_at")),
                    pushed_at=self._parse_datetime(data.get("pushed_at")),
                    # Collection-Metadaten
                    collected_at=now_utc,
                    stars_at_collection=stars_now,
                )
                new_repositories.append(repo)

            if new_repositories:
                sess.add_all(new_repositories)
                # Phase 4.1: Prometheus-Metrik für neue Repositories
                record_db_operation(
                    table="repositories", operation="insert", count=len(new_repositories)
                )

            # Phase 4.1: Prometheus-Metrik für aktualisierte Repositories
            if updated_count > 0:
                record_db_operation(table="repositories", operation="update", count=updated_count)

            total_processed = len(new_repositories) + updated_count
            prefix = f"[{batch_id}] " if batch_id else ""
            if skipped_missing_owner > 0:
                logger.warning(
                    "%sBulk-Upsert Repositories: %s neue, %s aktualisiert, %s wegen fehlender "
                    "Owner übersprungen",
                    prefix,
                    len(new_repositories),
                    updated_count,
                    skipped_missing_owner,
                )
            else:
                logger.info(
                    "%sBulk-Upsert Repositories: %s neue, %s aktualisiert, 0 übersprungen",
                    prefix,
                    len(new_repositories),
                    updated_count,
                )

            return total_processed

        return self._run_in_session(_operation, session=session)

    def _parse_datetime(self, dt_str: Optional[str]) -> Optional[datetime]:
        """
        Konvertiert einen Datetime-String in ein datetime-Objekt.

        Args:
            dt_str: Datetime-String oder None

        Returns:
            datetime-Objekt oder None
        """
        if not dt_str:
            return None

        if isinstance(dt_str, datetime):
            return dt_str

        try:
            # ISO-Format-String parsen
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError) as e:
            logger.warning(f"Fehler beim Parsen des Datetime-Strings '{dt_str}': {e}")
            return None

    # Statistik-Methoden

    def get_repository_count(self, session: Optional[Session] = None) -> int:
        """Anzahl der Repositories in der Datenbank."""

        def _operation(sess: Session) -> int:
            try:
                return sess.query(Repository).count()
            except Exception as e:
                logger.error(f"Fehler beim Abrufen der Repository-Anzahl: {e}")
                # Versuche, die Session zurückzusetzen und erneut abzufragen
                try:
                    sess.rollback()
                    return sess.query(Repository).count()
                except Exception as retry_error:
                    logger.error(
                        f"Erneuter Fehler beim Abrufen der Repository-Anzahl: {retry_error}"
                    )
                    return 0

        return self._run_in_session(_operation, session=session)

    def get_contributor_count(self, session: Optional[Session] = None) -> int:
        """Anzahl der Contributors in der Datenbank."""

        def _operation(sess: Session) -> int:
            try:
                return sess.query(Contributor).count()
            except Exception as e:
                logger.error(f"Fehler beim Abrufen der Contributor-Anzahl: {e}")
                # Versuche, die Session zurückzusetzen und erneut abzufragen
                try:
                    sess.rollback()
                    return sess.query(Contributor).count()
                except Exception as retry_error:
                    logger.error(
                        f"Erneuter Fehler beim Abrufen der Contributor-Anzahl: {retry_error}"
                    )
                    return 0

        return self._run_in_session(_operation, session=session)

    def get_organization_count(self, session: Optional[Session] = None) -> int:
        """Anzahl der Organisationen in der Datenbank."""

        def _operation(sess: Session) -> int:
            try:
                return sess.query(Organization).count()
            except Exception as e:
                logger.error(f"Fehler beim Abrufen der Organisations-Anzahl: {e}")
                # Versuche, die Session zurückzusetzen und erneut abzufragen
                try:
                    sess.rollback()
                    return sess.query(Organization).count()
                except Exception as retry_error:
                    logger.error(
                        f"Erneuter Fehler beim Abrufen der Organisations-Anzahl: {retry_error}"
                    )
                    return 0

        return self._run_in_session(_operation, session=session)

    def get_language_statistics(
        self,
        session: Optional[Session] = None,
    ) -> List[Tuple[str, int]]:
        """
        Statistik über die verwendeten Programmiersprachen.

        Returns:
            Liste von Tupeln (Sprache, Anzahl), sortiert nach Anzahl absteigend
        """

        def _operation(sess: Session) -> List[Tuple[str, int]]:
            try:
                result = (
                    sess.query(Repository.language, func.count(Repository.id).label("count"))
                    .filter(Repository.language.isnot(None))
                    .group_by(Repository.language)
                    .order_by(desc("count"))
                    .all()
                )
                return list(result)
            except Exception as e:
                logger.error(f"Fehler beim Abrufen der Sprachstatistiken: {e}")
                # Versuche, die Session zurückzusetzen und erneut abzufragen
                try:
                    sess.rollback()
                    result = (
                        sess.query(Repository.language, func.count(Repository.id).label("count"))
                        .filter(Repository.language.isnot(None))
                        .group_by(Repository.language)
                        .order_by(desc("count"))
                        .all()
                    )
                    return list(result)
                except Exception as retry_error:
                    logger.error(
                        f"Erneuter Fehler beim Abrufen der Sprachstatistiken: {retry_error}"
                    )
                    return []

        return self._run_in_session(_operation, session=session)

    def get_repository_date_range(
        self,
        session: Optional[Session] = None,
    ) -> Tuple[Optional[datetime], Optional[datetime]]:
        """
        Datumsbereich der Repositories.

        Returns:
            Tupel (ältestes Erstellungsdatum, neuestes Erstellungsdatum)
        """

        def _operation(sess: Session) -> Tuple[Optional[datetime], Optional[datetime]]:
            try:
                min_date = sess.query(func.min(Repository.created_at)).scalar()
                max_date = sess.query(func.max(Repository.created_at)).scalar()
                return (min_date, max_date)
            except Exception as e:
                logger.error(f"Fehler beim Abrufen des Repository-Datumsbereichs: {e}")
                # Versuche, die Session zurückzusetzen und erneut abzufragen
                try:
                    sess.rollback()
                    min_date = sess.query(func.min(Repository.created_at)).scalar()
                    max_date = sess.query(func.max(Repository.created_at)).scalar()
                    return (min_date, max_date)
                except Exception as retry_error:
                    logger.error(
                        f"Erneuter Fehler beim Abrufen des Repository-Datumsbereichs: {retry_error}"
                    )
                    return (None, None)

        return self._run_in_session(_operation, session=session)

    def get_contributor_location_stats(
        self,
        session: Optional[Session] = None,
    ) -> Dict[str, Union[int, float]]:
        """
        Statistik über Contributor-Standorte.

        Returns:
            Dictionary mit Statistiken
        """

        def _operation(sess: Session) -> Dict[str, Union[int, float]]:
            try:
                total = self.get_contributor_count(session=sess)

                try:
                    with_location = (
                        sess.query(Contributor)
                        .filter(
                            Contributor.location.isnot(None),
                            Contributor.location != "",
                        )
                        .count()
                    )
                except Exception as e:
                    logger.error(f"Fehler beim Abrufen der Contributors mit Standort: {e}")
                    sess.rollback()
                    with_location = 0

                try:
                    with_country_code = (
                        sess.query(Contributor)
                        .filter(
                            Contributor.country_code.isnot(None),
                            Contributor.country_code != "",
                        )
                        .count()
                    )
                except Exception as e:
                    logger.error(f"Fehler beim Abrufen der Contributors mit Ländercode: {e}")
                    sess.rollback()
                    with_country_code = 0

                stats = {
                    "total": total,
                    "with_location": with_location,
                    "with_country_code": with_country_code,
                    "location_percentage": (with_location / total * 100) if total > 0 else 0,
                    "country_code_percentage": (
                        (with_country_code / total * 100) if total > 0 else 0
                    ),
                    "country_code_from_location_percentage": (
                        (with_country_code / with_location * 100) if with_location > 0 else 0
                    ),
                }

                return stats
            except Exception as e:
                logger.error(f"Fehler beim Abrufen der Contributor-Standortstatistiken: {e}")
                return {
                    "total": 0,
                    "with_location": 0,
                    "with_country_code": 0,
                    "location_percentage": 0,
                    "country_code_percentage": 0,
                    "country_code_from_location_percentage": 0,
                }

        return self._run_in_session(_operation, session=session)

    # --- Export-Hilfen ---

    def get_data_for_export(
        self,
        table_name: str,
        limit: Optional[int] = None,
        session: Optional[Session] = None,
    ) -> List[Base]:
        """Liefert Datensätze für den CSV-Export generisch über den Tabellennamen.

        Args:
            table_name: Name der Tabelle (z.B. "repositories", "contributors").
            limit: Optionale Begrenzung der Anzahl der Datensätze.
            session: Optionale bestehende Session.
        """

        if table_name not in _MODEL_REGISTRY:
            raise ValueError(f"Unbekannte Tabelle für Export: {table_name}")

        model = _MODEL_REGISTRY[table_name]

        def _operation(sess: Session) -> List[Base]:
            query = sess.query(model)
            if limit is not None:
                query = query.limit(limit)
            return list(query.all())

        return self._run_in_session(_operation, session=session)

    # --- Geocoding / Location-Hilfen ---

    def get_entities_without_location(
        self,
        entity_type: str,
        limit: int = 100,
        session: Optional[Session] = None,
    ) -> List[Union[Contributor, Organization]]:
        """Liefert Entities mit gesetzter Location aber ohne Country Code.

        Args:
            entity_type: "contributor" oder "organization".
            limit: Maximale Anzahl der Einträge.
            session: Optionale bestehende Session.
        """

        entity_type_lower = entity_type.lower()
        if entity_type_lower == "contributor":
            model = Contributor
        elif entity_type_lower == "organization":
            model = Organization
        else:
            raise ValueError(f"Unbekannter Entity-Typ: {entity_type}")

        def _operation(sess: Session) -> List[Union[Contributor, Organization]]:
            query = sess.query(model).filter(
                model.location.isnot(None),
                model.location != "",
                model.country_code.is_(None),
            )
            if limit is not None:
                query = query.limit(limit)
            return list(query.all())

        return self._run_in_session(_operation, session=session)

    def get_entities_for_geocoding(  # noqa: C901
        self,
        entity_type: str,
        *,
        missing_country_only: bool = True,
        include_marked_empty: bool = False,
        after_id: Optional[int] = None,
        created_from: Optional[datetime] = None,
        created_to: Optional[datetime] = None,
        min_stars: Optional[int] = None,
        max_stars: Optional[int] = None,
        limit: Optional[int] = 100,
        session: Optional[Session] = None,
    ) -> List[Union[Contributor, Organization]]:
        """Liefert Entities für Geocoding unter Berücksichtigung optionaler Filter.

        Args:
            entity_type: "contributor" oder "organization".
            missing_country_only: Wenn True, nur Einträge ohne Country Code.
            include_marked_empty: Wenn True, zusätzlich Einträge, die ein früherer Lauf
                mit leerem Country Code ("") als "kein geocodierbares Land" markiert hat.
                Wirkt nur zusammen mit missing_country_only.
            after_id: Nur Einträge mit größerer ID liefern. Die Ergebnisse sind nach ID
                sortiert; der Aufrufer blättert damit durch die Tabelle, ohne dass ein
                Eintrag, der nach der Verarbeitung weiterhin zur Auswahl passt, erneut
                geliefert wird.
            created_from: Untere Grenze für Repository-Erstellungszeit (inklusive).
            created_to: Obere Grenze für Repository-Erstellungszeit (exklusive).
            min_stars: Minimale Anzahl an Sternen (stars_at_collection) eines zugehörigen Repos.
            max_stars: Maximale Anzahl an Sternen (stars_at_collection) eines zugehörigen Repos.
            limit: Maximale Anzahl der Einträge.
            session: Optionale bestehende Session.
        """

        entity_type_lower = entity_type.lower()
        if entity_type_lower == "contributor":
            model = Contributor
        elif entity_type_lower == "organization":
            model = Organization
        else:
            raise ValueError(f"Unbekannter Entity-Typ: {entity_type}")

        def _operation(sess: Session) -> List[Union[Contributor, Organization]]:
            query = sess.query(model).filter(
                model.location.isnot(None),
                model.location != "",
            )

            if missing_country_only:
                # Nur Einträge ohne gesetzten Country Code (NULL) berücksichtigen.
                # Einträge mit leerem String ("") wurden bereits verarbeitet und
                # explizit als "kein geocodierbares Land" markiert und sollen
                # daher nicht erneut in die Geocoding-Pipeline gelangen - außer der
                # Aufrufer verlangt es ausdrücklich (include_marked_empty).
                query = query.filter(_missing_country_filter(model, include_marked_empty))

            if after_id is not None:
                query = query.filter(model.id > after_id)

            # Filter auf verbundene Repositories
            # (Sterne und Erstellungszeit des Repositories)
            if (
                min_stars is not None
                or max_stars is not None
                or created_from is not None
                or created_to is not None
            ):
                # Verknüpfe über Repositories, damit mindestens ein Repo im gewünschten
                # Bereich existiert.
                if model is Contributor:
                    owner_column = Repository.owner_id
                    entity_id_column = Contributor.id
                else:
                    owner_column = Repository.organization_id
                    entity_id_column = Organization.id

                repo_filters = [owner_column == entity_id_column]
                if min_stars is not None:
                    repo_filters.append(Repository.stars_at_collection >= min_stars)
                if max_stars is not None:
                    repo_filters.append(Repository.stars_at_collection <= max_stars)
                if created_from is not None:
                    repo_filters.append(Repository.created_at >= created_from)
                if created_to is not None:
                    repo_filters.append(Repository.created_at < created_to)

                query = query.filter(exists().where(and_(*repo_filters)))

            query = query.order_by(model.id)
            if limit is not None:
                query = query.limit(limit)

            return list(query.all())

        return self._run_in_session(_operation, session=session)

    # --- Maintenance / Cleanup ---

    def delete_orphaned_owners(
        self,
        dry_run: bool = False,
        session: Optional[Session] = None,
    ) -> Tuple[int, int]:
        """Entfernt Contributors und Organisationen ohne zugehörige Repositories.

        Contributors, die über `repository_contributors` als Mitwirkende eines
        Repositories referenziert werden, gelten nicht als verwaist.
        Eine Organisation gilt nur als verwaist, wenn kein Repository sie als Besitzer
        (`owner_id`) oder über `organization_id` nennt.

        Args:
            dry_run: Wenn True, werden keine Änderungen vorgenommen, sondern nur
                geloggt, welche Einträge entfernt würden.
            session: Optionale bestehende Session.

        Returns:
            Tuple mit der Anzahl der entfernten Contributors und Organisationen; im
            Probelauf die Anzahl derer, die entfernt würden.
        """

        def _operation(sess: Session) -> Tuple[int, int]:
            # Finde Contributors ohne zugehörige Repositories. Ein Contributor gilt nur dann
            # als verwaist, wenn er weder ein Repository besitzt noch über
            # repository_contributors als Mitwirkender referenziert wird - die Tabelle
            # hält eine Zeile pro GitHub-Konto, unabhängig von dessen Rolle.
            # github_user_id deckt Mitwirkende ab, deren contributor_id noch nicht
            # nachgezogen wurde. Die NULL-Filter sind nötig: ein NULL in der Unterabfrage
            # (anonyme Autoren) würde NOT IN für alle Zeilen unentscheidbar machen.
            orphaned_contributors = (
                sess.query(Contributor)
                .filter(~Contributor.id.in_(sess.query(Repository.owner_id)))
                .filter(
                    ~Contributor.id.in_(
                        sess.query(RepositoryContributor.contributor_id).filter(
                            RepositoryContributor.contributor_id.isnot(None)
                        )
                    )
                )
                .filter(
                    ~Contributor.id.in_(
                        sess.query(RepositoryContributor.github_user_id).filter(
                            RepositoryContributor.github_user_id.isnot(None)
                        )
                    )
                )
                .all()
            )
            orphaned_contributors_count = len(orphaned_contributors)

            if orphaned_contributors_count:
                logger.info("%d verwaiste Contributors gefunden", orphaned_contributors_count)
                for contributor in orphaned_contributors:
                    logger.debug(
                        "Verwaister Contributor: %s (ID: %s)",
                        contributor.login,
                        contributor.id,
                    )
            else:
                logger.info("Keine verwaisten Contributors gefunden")

            # Finde Organisationen ohne zugehörige Repositories. Besitz zählt über owner_id:
            # Repositories, die vor 2026-10-13 über die Suche gesammelt wurden, haben keine
            # organization_id; nur über diese Spalte zu prüfen, hielt auf der Produktion
            # 410.884 Organisationen mit Repositories für verwaist. Der NULL-Filter ist
            # nötig: Repositories von Nutzerkonten haben keine organization_id, und ein NULL
            # in der Unterabfrage würde NOT IN für alle Zeilen unentscheidbar machen.
            orphaned_organizations = (
                sess.query(Organization)
                .filter(~Organization.id.in_(sess.query(Repository.owner_id)))
                .filter(
                    ~Organization.id.in_(
                        sess.query(Repository.organization_id).filter(
                            Repository.organization_id.isnot(None)
                        )
                    )
                )
                .all()
            )
            orphaned_organizations_count = len(orphaned_organizations)

            if orphaned_organizations_count:
                logger.info("%d verwaiste Organisationen gefunden", orphaned_organizations_count)
                for organization in orphaned_organizations:
                    logger.debug(
                        "Verwaiste Organisation: %s (ID: %s)",
                        organization.login,
                        organization.id,
                    )
            else:
                logger.info("Keine verwaisten Organisationen gefunden")

            removed_contributors = 0
            removed_organizations = 0

            if not dry_run:
                for contributor in orphaned_contributors:
                    sess.delete(contributor)
                    removed_contributors += 1

                for organization in orphaned_organizations:
                    sess.delete(organization)
                    removed_organizations += 1

                if removed_contributors or removed_organizations:
                    logger.info(
                        "%d Contributors und %d Organisationen entfernt",
                        removed_contributors,
                        removed_organizations,
                    )
                else:
                    logger.info("Keine verwaisten Owner-Einträge zum Löschen gefunden")
            else:
                logger.info(
                    "Simulationsmodus: %d Contributors und %d Organisationen "
                    "würden entfernt werden",
                    orphaned_contributors_count,
                    orphaned_organizations_count,
                )
                return orphaned_contributors_count, orphaned_organizations_count

            return removed_contributors, removed_organizations

        return self._run_in_session(_operation, session=session)

    def get_organization_location_stats(
        self,
        session: Optional[Session] = None,
    ) -> Dict[str, Union[int, float]]:
        """
        Statistik über Organisations-Standorte.

        Returns:
            Dictionary mit Statistiken
        """

        def _operation(sess: Session) -> Dict[str, Union[int, float]]:
            try:
                total = self.get_organization_count(session=sess)

                try:
                    with_location = (
                        sess.query(Organization)
                        .filter(
                            Organization.location.isnot(None),
                            Organization.location != "",
                        )
                        .count()
                    )
                except Exception as e:
                    logger.error(f"Fehler beim Abrufen der Organisationen mit Standort: {e}")
                    sess.rollback()
                    with_location = 0

                try:
                    with_country_code = (
                        sess.query(Organization)
                        .filter(
                            Organization.country_code.isnot(None),
                            Organization.country_code != "",
                        )
                        .count()
                    )
                except Exception as e:
                    logger.error(f"Fehler beim Abrufen der Organisationen mit Ländercode: {e}")
                    sess.rollback()
                    with_country_code = 0

                stats = {
                    "total": total,
                    "with_location": with_location,
                    "with_country_code": with_country_code,
                    "location_percentage": (with_location / total * 100) if total > 0 else 0,
                    "country_code_percentage": (
                        (with_country_code / total * 100) if total > 0 else 0
                    ),
                    "country_code_from_location_percentage": (
                        (with_country_code / with_location * 100) if with_location > 0 else 0
                    ),
                }

                return stats
            except Exception as e:
                logger.error(f"Fehler beim Abrufen der Organisations-Standortstatistiken: {e}")
                return {
                    "total": 0,
                    "with_location": 0,
                    "with_country_code": 0,
                    "location_percentage": 0,
                    "country_code_percentage": 0,
                    "country_code_from_location_percentage": 0,
                }

        return self._run_in_session(_operation, session=session)
