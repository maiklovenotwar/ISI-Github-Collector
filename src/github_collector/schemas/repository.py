"""Pydantic Schema für Repository-Validierung.

Phase 3.1: Schema-Validierung mit Pydantic
"""

from typing import Optional

from pydantic import ConfigDict, Field, field_validator

from .common import GitHubEntity, TimestampMixin
from .owner import OwnerSchema


class RepositorySchema(GitHubEntity, TimestampMixin):
    """Schema für Repository-Daten aus der GitHub API.

    Validiert die Response von:
    - GET /repos/{owner}/{repo}
    - GET /search/repositories
    """

    # Basis-Informationen
    name: str = Field(..., min_length=1, max_length=100, description="Repository-Name")
    full_name: str = Field(
        ..., min_length=1, max_length=255, description="Vollständiger Name (owner/repo)"
    )
    private: bool = Field(False, description="Ist privates Repository")
    description: Optional[str] = Field(
        None, max_length=65535, description="Repository-Beschreibung"
    )
    fork: bool = Field(False, description="Ist ein Fork")

    # Owner (nested)
    owner: OwnerSchema = Field(..., description="Repository Owner (User/Organization)")

    # URLs
    html_url: Optional[str] = Field(None, description="GitHub Web-URL")
    clone_url: Optional[str] = Field(None, description="Git Clone URL")
    homepage: Optional[str] = Field(None, max_length=1000, description="Projekt-Homepage")

    # Statistiken
    stargazers_count: int = Field(0, ge=0, description="Anzahl Stars")
    watchers_count: int = Field(0, ge=0, description="Anzahl Watchers")
    forks_count: int = Field(0, ge=0, description="Anzahl Forks")
    open_issues_count: int = Field(0, ge=0, description="Anzahl offener Issues")
    size: int = Field(0, ge=0, description="Repository-Größe in KB")

    # Technische Details
    language: Optional[str] = Field(None, max_length=50, description="Primäre Programmiersprache")
    default_branch: str = Field("main", max_length=255, description="Default Branch")
    has_issues: bool = Field(True, description="Issues aktiviert")
    has_wiki: bool = Field(True, description="Wiki aktiviert")
    has_pages: bool = Field(False, description="GitHub Pages aktiviert")
    has_downloads: bool = Field(True, description="Downloads aktiviert")
    archived: bool = Field(False, description="Ist archiviert")
    disabled: bool = Field(False, description="Ist deaktiviert")

    # Zeitstempel (zusätzlich zu created_at/updated_at aus TimestampMixin)
    pushed_at: Optional[str] = Field(None, description="Letzter Push")

    # Lizenz
    license: Optional[dict] = Field(None, description="Lizenz-Information")

    @field_validator("description", mode="before")
    @classmethod
    def normalize_description(cls, v: Optional[str]) -> Optional[str]:
        """Normalisiere Beschreibung.

        - None → None (nicht leerer String)
        - Leerer String → None
        - Trimme Whitespace
        - Kürze auf MySQL TEXT Limit (65535 Zeichen)

        Args:
            v: Beschreibung oder None

        Returns:
            Bereinigte Beschreibung oder None
        """
        if v is None or v == "":
            return None
        v = v.strip()
        if not v:
            return None
        # Kürze auf MySQL TEXT Limit
        if len(v) > 65535:
            v = v[:65532] + "..."
        return v

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, v: Optional[str]) -> Optional[str]:
        """Normalisiere Programmiersprache.

        Args:
            v: Sprache oder None

        Returns:
            Bereinigte Sprache oder None
        """
        if v is None or v == "":
            return None
        return v.strip()

    @field_validator("homepage", "html_url", "clone_url", mode="before")
    @classmethod
    def validate_url(cls, v: Optional[str]) -> Optional[str]:
        """Validiere URL-Felder.

        Args:
            v: URL oder None

        Returns:
            Bereinigte URL oder None
        """
        if v is None or v == "":
            return None
        v = v.strip()
        if not v:
            return None
        return v

    def get_owner_id(self) -> int:
        """Extrahiere Owner-ID für DB-Zuordnung.

        Returns:
            Owner-ID
        """
        return self.owner.id

    def get_owner_login(self) -> str:
        """Extrahiere Owner-Login für DB-Zuordnung.

        Returns:
            Owner-Login (normalisiert)
        """
        return self.owner.login

    def is_organization_owned(self) -> bool:
        """Prüfe ob Repository einer Organization gehört.

        Returns:
            True wenn Owner eine Organization ist
        """
        return self.owner.type == "Organization"

    model_config = ConfigDict(
        extra="ignore",
        use_enum_values=True,
    )
