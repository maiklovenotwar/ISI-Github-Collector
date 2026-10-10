"""Pydantic Schema für GitHub Owner (User/Organization).

Phase 3.1: Schema-Validierung mit Pydantic
"""

from enum import Enum
from typing import Optional

from pydantic import ConfigDict, Field, field_validator

from .common import GitHubEntity


class OwnerType(str, Enum):
    """GitHub Owner-Typ."""

    USER = "User"
    ORGANIZATION = "Organization"
    BOT = "Bot"


class OwnerSchema(GitHubEntity):
    """Schema für GitHub Repository Owner (User oder Organization).

    Dieses Schema validiert die `owner`-Felder aus der GitHub API Response.
    Es wird sowohl für Contributors als auch Organizations verwendet.
    """

    login: str = Field(..., min_length=1, max_length=39, description="GitHub Login-Name")
    avatar_url: Optional[str] = Field(None, description="Avatar-URL")
    html_url: Optional[str] = Field(None, description="GitHub Profil-URL")
    type: OwnerType = Field(..., description="Owner-Typ (User/Organization/Bot)")

    # Zusätzliche GitHub API Felder (optional)
    url: Optional[str] = Field(None, description="API-URL")
    site_admin: bool = Field(False, description="Ist GitHub Site Admin")

    @field_validator("login")
    @classmethod
    def normalize_login(cls, v: str) -> str:
        """Normalisiere Login zu lowercase für konsistente Vergleiche.

        GitHub Login-Namen sind case-insensitive, aber die API kann
        unterschiedliche Schreibweisen liefern.

        Args:
            v: Login-Name

        Returns:
            Lowercase Login-Name
        """
        return v.lower().strip()

    @field_validator("avatar_url", "html_url", "url", mode="before")
    @classmethod
    def validate_url(cls, v: Optional[str]) -> Optional[str]:
        """Validiere und bereinige URL-Felder.

        Args:
            v: URL als String oder None

        Returns:
            Bereinigte URL oder None
        """
        if v is None or v == "":
            return None
        # Stelle sicher, dass URL mit http:// oder https:// startet
        if not v.startswith(("http://", "https://")):
            return None
        return v.strip()

    model_config = ConfigDict(
        extra="ignore",
        use_enum_values=True,
    )
