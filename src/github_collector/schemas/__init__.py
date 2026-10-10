"""Pydantic Schemas für Datenvalidierung.

Dieses Modul enthält Pydantic-Schemas zur Validierung von:
- Repository-Daten (GitHub API → Database)
- Owner-Daten (User/Organization)
- Gemeinsame Basis-Typen

Phase 3.1: Schema-Validierung mit Pydantic
"""

from .common import GitHubEntity, TimestampMixin
from .owner import OwnerSchema, OwnerType
from .repository import RepositorySchema

__all__ = [
    # Base Classes
    "GitHubEntity",
    "TimestampMixin",
    # Owner
    "OwnerSchema",
    "OwnerType",
    # Repository
    "RepositorySchema",
]
