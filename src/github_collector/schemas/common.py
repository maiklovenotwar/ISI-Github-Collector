"""Gemeinsame Basis-Typen und Hilfsfunktionen für Pydantic-Schemas.

Phase 3.1: Schema-Validierung mit Pydantic
"""

from datetime import datetime
from typing import Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GitHubEntity(BaseModel):
    """Basis-Schema für alle GitHub-Entitäten mit gemeinsamen Feldern."""

    id: int = Field(..., description="Eindeutige GitHub-ID")
    node_id: Optional[str] = Field(None, description="GraphQL Node-ID")

    model_config = ConfigDict(
        extra="ignore",
        use_enum_values=True,
    )


class TimestampMixin(BaseModel):
    """Mixin für Zeitstempel-Felder."""

    created_at: datetime = Field(..., description="Erstellungszeitpunkt")
    updated_at: datetime = Field(..., description="Letztes Update")

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def parse_github_timestamp(cls, v: Union[str, datetime]) -> datetime:
        """Parse GitHub API Timestamp (ISO 8601).

        Args:
            v: Timestamp als String oder datetime

        Returns:
            Parsed datetime object

        Raises:
            ValueError: Wenn Timestamp ungültig ist
        """
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            # GitHub liefert ISO 8601 Format: "2023-11-01T12:34:56Z"
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        raise ValueError(f"Ungültiger Timestamp: {v}")
