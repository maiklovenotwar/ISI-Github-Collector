"""Datenbankmodelle für GitHub-Daten."""

import logging

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import declarative_base, relationship

logger = logging.getLogger(__name__)

Base = declarative_base()


class Contributor(Base):
    """Contributor-Modell für GitHub-Benutzer."""

    __tablename__ = "contributors"

    id = Column(Integer, primary_key=True)
    login = Column(String(255), nullable=False, unique=True)
    name = Column(String(255))
    email = Column(String(255))
    type = Column(String(50))
    avatar_url = Column(String(255))
    company = Column(String(255))
    blog = Column(String(255))
    location = Column(String(255))
    country_code = Column(String(2), index=True)  # ISO-Ländercode
    region = Column(String(100))  # Region innerhalb des Landes oder Kontinents
    # ccTLD-Anreicherung (scripts/enrichment/enrich_cctld.py --entity contributors) - siehe
    # add_cctld_contributors_no_index.py; bewusst ohne Index, anders als bei Organization.
    country_code_cctld = Column(String(2))
    cctld_source = Column(String(20))
    cctld_enriched_at = Column(DateTime)
    cctld_run_id = Column(String(36))
    bio = Column(Text)
    twitter_username = Column(String(50))
    public_repos = Column(Integer)
    public_gists = Column(Integer)
    followers = Column(Integer)
    following = Column(Integer)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    # Zeitpunkt des letzten Profil-Abrufversuchs (auch bei leerem Ergebnis gesetzt): viele
    # echte Profile haben name/location/company alle leer, diese Spalte unterscheidet
    # "nie versucht" von "versucht, nichts gefunden".
    enrichment_checked_at = Column(DateTime, index=True)

    # Beziehungen
    owned_repositories = relationship(
        "Repository", foreign_keys="Repository.owner_id", back_populates="owner"
    )
    repository_contributions = relationship("RepositoryContributor", back_populates="contributor")


class Organization(Base):
    """Organisations-Modell für GitHub-Organisationen."""

    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True)
    login = Column(String(255), nullable=False, unique=True)
    name = Column(String(255))
    email = Column(String(255))
    type = Column(String(50))
    avatar_url = Column(String(255))
    company = Column(String(255))
    blog = Column(String(255))
    location = Column(String(255))
    country_code = Column(String(2), index=True)  # ISO-Ländercode
    region = Column(String(100))  # Region innerhalb des Landes oder Kontinents
    bio = Column(Text)
    twitter_username = Column(String(50))
    public_repos = Column(Integer)
    public_gists = Column(Integer)
    followers = Column(Integer)
    following = Column(Integer)
    public_members = Column(Integer)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

    # ccTLD-Anreicherung (scripts/enrichment/enrich_cctld.py) - siehe add_cctld_columns.sql;
    # mit Index auf cctld_run_id (idx_organizations_cctld_run_id), anders als bei Contributor.
    country_code_cctld = Column(String(2))
    cctld_source = Column(String(20))
    cctld_enriched_at = Column(DateTime)
    cctld_run_id = Column(String(36), index=True)
    # Zeitpunkt des letzten Profil-Abrufversuchs durch scripts/enrichment/enrich_org_data_graphql.py
    # (auch gesetzt, wenn GitHub die Organisation nicht mehr kennt) - gleiche Bedeutung wie
    # Contributor.enrichment_checked_at. Migration:
    # scripts/migrations/add_organizations_enrichment_checked_at.sql
    enrichment_checked_at = Column(DateTime)

    # Orbis Enrichment
    bvd_id = Column(String(50), index=True)  # Orbis BvD-Identifier
    country_code_orbis = Column(String(2))  # ISO-Ländercode aus Orbis (Ground-Truth)
    # Legacy-Spalte, wird nicht mehr geschrieben (siehe OrbisEnrichment.match_type)
    orbis_match_type = Column(String(20))
    orbis_enriched_at = Column(DateTime)  # Zeitstempel des aktuellen Orbis-Matches
    # Legacy-Spalte, wird nicht mehr geschrieben (siehe OrbisEnrichment.run_id). Vorhandene
    # Werte stammen aus früheren Läufen von scripts/enrichment/enrich_org_data_graphql.py.
    orbis_run_id = Column(String(36), index=True)
    # "Match-ID": zeigt auf die aktuell aktive Zeile in orbis_enrichments
    # (NULL nach einem Rollback). Die referenzierte Zeile selbst bleibt beim
    # Rollback bestehen (siehe OrbisEnrichment.reverted_at) - so lässt sich
    # jeder Match jederzeit nachprüfen, auch nachdem er zurückgerollt wurde.
    current_orbis_enrichment_id = Column(
        Integer, ForeignKey("orbis_enrichments.id", ondelete="SET NULL"), nullable=True
    )

    # Beziehungen
    repositories = relationship("Repository", back_populates="organization")
    # foreign_keys explizit nötig: seit current_orbis_enrichment_id gibt es
    # zwei FK-Pfade zwischen organizations und orbis_enrichments (der hier
    # über OrbisEnrichment.organization_id, nicht über
    # Organization.current_orbis_enrichment_id).
    orbis_enrichments = relationship(
        "OrbisEnrichment",
        back_populates="organization",
        cascade="all, delete-orphan",
        foreign_keys="OrbisEnrichment.organization_id",
    )


class OrbisEnrichment(Base):
    """Lauf-Historie für Orbis-Matching-Ergebnisse ("Hybrid-Ansatz").

    Ergänzt die drei Ergebnis-Spalten auf `Organization` (bvd_id,
    country_code_orbis, orbis_enriched_at) um eine pro-Lauf-Historie mit
    Rollback-Fähigkeit über `run_id`. Umsetzung des Entwurfs aus
    Orbis-Notizen (intern, ältere Entwürfe): enrichment/
    orbis_matching_strategy_v2.md (Abschnitt 8), der nie implementiert wurde.

    Bewusst getrennt von den Legacy-Spalten `orbis_match_type`/`orbis_run_id`
    auf `Organization` (diese bleiben unangetastet; `orbis_run_id` enthält
    dort Lauf-Kennungen früherer Läufe von scripts/enrichment/enrich_org_data_graphql.py,
    das die Spalte bis zur Migration add_organizations_enrichment_checked_at.sql
    für einen unabhängigen Zweck verwendet hat).

    WICHTIG (Fund beim Testen des Rollbacks): Zeilen werden bei einem
    Rollback NICHT mehr gelöscht, nur `reverted_at` gesetzt - sonst gäbe es
    nach einem Rollback nirgendwo mehr eine Spur, welche GitHub-Organisation
    ursprünglich mit welcher Orbis-Firma verknüpft war. Ein Logbuch, das
    seine eigenen Einträge löscht, taugt nicht zur Nachprüfung.

    WICHTIG (zweiter Fund, direkt danach): eine separate `OrbisCompany`-
    Cache-Tabelle (Name/Ort/Website pro BVDID) wurde zunächst als eigene
    Tabelle gebaut, aber wieder entfernt - sie erforderte für die
    naheliegendste Frage ("welche GH-Org gehört zu diesem Match, wie heißt
    die Firma?") einen JOIN über zwei Tabellen, und wusste selbst nichts
    über die GH-Seite. Company-Snapshot-Felder (company_name/_country/
    _city/_website) leben deshalb DIREKT hier - jede Zeile beantwortet die
    Frage komplett aus sich selbst heraus, ohne Join. Eine reine
    BVDID-Cache-Tabelle passt konzeptionell nicht zu diesem Logbuch: sie
    hätte nur eine Zeile pro Firma, dieses Modell aber eine Zeile pro
    Match-VERSUCH (inkl. zurückgerollter) - die Firmendaten pro Versuch
    festzuhalten ist ohnehin historisch korrekter, falls sich der
    Firmenname bei Orbis später ändert.
    """

    __tablename__ = "orbis_enrichments"

    id = Column(Integer, primary_key=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bvd_id = Column(String(50), nullable=False, index=True)
    company_name = Column(String(255))  # NAME_NATIVE zum Zeitpunkt des Matches
    company_country = Column(String(2))  # CTRYISO
    company_city = Column(String(255))  # CITY_NATIVE
    company_website = Column(String(255))  # WEBSITE
    match_type = Column(String(20), nullable=False, index=True)  # combined_high, combined_low, ...
    hq_method = Column(String(30))  # ROOT_DOMAIN_PRESENCE, BVDID_FALLBACK, ...
    # Match-Status beim Match (unique, consensus, pending; siehe CONTEXT.md). Ohne ihn
    # waren unique und consensus nicht unterscheidbar (Befund H5).
    match_status = Column(String(20), nullable=True)
    score = Column(Integer, nullable=False, index=True)
    blog_matched = Column(Boolean, nullable=False, default=False)
    run_id = Column(String(36), nullable=False, index=True)  # UUID des Enrichment-Laufs
    enriched_at = Column(DateTime, nullable=False)
    reverted_at = Column(DateTime, nullable=True)  # NULL = weiterhin aktiv
    created_at = Column(DateTime)

    __table_args__ = (UniqueConstraint("organization_id", "run_id", name="uk_org_run"),)

    # Beziehungen (foreign_keys explizit nötig, siehe Organization.orbis_enrichments)
    organization = relationship(
        "Organization",
        back_populates="orbis_enrichments",
        foreign_keys=[organization_id],
    )


class Repository(Base):
    """Repository-Modell für GitHub-Repositories."""

    __tablename__ = "repositories"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False, unique=True)
    owner_id = Column(Integer, ForeignKey("contributors.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True)

    # Metadaten
    @declared_attr
    def description(self):
        # Verwendet standardmäßig Text, mit LONGTEXT als Variante für den MySQL-Dialekt.
        return Column(Text().with_variant(LONGTEXT(), "mysql"), nullable=True)

    homepage = Column(String(255))
    language = Column(String(100), index=True)
    private = Column(Boolean, default=False)
    fork = Column(Boolean, default=False)
    default_branch = Column(String(100))
    size = Column(Integer)

    # GitHub API-Statistiken
    stargazers_count = Column(Integer, default=0, index=True)
    watchers_count = Column(Integer, default=0)
    forks_count = Column(Integer, default=0)
    open_issues_count = Column(Integer, default=0)

    # Zusätzliche Statistiken
    contributors_count = Column(Integer)
    commits_count = Column(Integer)
    pull_requests_count = Column(Integer)

    # Zeitstempel (von GitHub)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    pushed_at = Column(DateTime)

    # Metadaten unserer Datenerhebung (Phase 2.1.7)
    collected_at = Column(DateTime, nullable=True, index=True)  # Zeitpunkt unseres Crawls
    stars_at_collection = Column(Integer, nullable=True)  # Stars zum Sammelzeitpunkt
    collection_status = Column(String(20), nullable=True)  # 'active', 'archived', 'deleted', etc.

    # Beziehungen
    owner = relationship(
        "Contributor", foreign_keys=[owner_id], back_populates="owned_repositories"
    )
    organization = relationship("Organization", back_populates="repositories")
    contributor_links = relationship(
        "RepositoryContributor", back_populates="repository", cascade="all, delete-orphan"
    )


class RepositoryContributor(Base):
    """Contributor-Netzwerk pro Repository (wer hat woran mitgewirkt, mit Commit-Anteil).

    Ergänzt die einzelne Owner-Beziehung auf `Repository` (`owner_id`) um die
    volle Contributor-Liste je Repo, analog zu `GET /repos/{owner}/{repo}/contributors`.
    Grundlage für Mikroebene-Netzwerkindikatoren (grenzüberschreitende Ko-Contribution),
    siehe docs/methodology/contributor_network.md.

    `contributor_id` ist bewusst nullable: anonyme Contributors (E-Mail-Autoren ohne
    verknüpften GitHub-Account, `anon=true`) und Logins, die noch nicht in der
    `contributors`-Tabelle erfasst sind, werden trotzdem über `github_login`
    gespeichert statt verworfen.
    """

    __tablename__ = "repository_contributors"

    id = Column(Integer, primary_key=True)
    repository_id = Column(
        Integer, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contributor_id = Column(
        Integer, ForeignKey("contributors.id", ondelete="SET NULL"), nullable=True, index=True
    )
    github_login = Column(String(255), nullable=False)
    github_user_id = Column(Integer, index=True)  # Stabile GitHub-User-ID (== contributors.id)
    github_type = Column(String(20))  # Account-Typ laut API (User, Bot, ...)
    contributions = Column(Integer)  # Commit-Anzahl auf dem Default-Branch laut GitHub API
    is_anonymous = Column(Boolean, nullable=False, default=False)
    collected_at = Column(DateTime, nullable=False)
    run_id = Column(String(36), index=True)  # UUID des Sammel-Laufs (für Revert)

    __table_args__ = (
        UniqueConstraint("repository_id", "github_login", name="uk_repo_contributor_login"),
    )

    # Beziehungen
    repository = relationship("Repository", back_populates="contributor_links")
    contributor = relationship("Contributor", back_populates="repository_contributions")
