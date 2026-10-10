"""Tests für Pydantic Schemas (Phase 3.1)."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from github_collector.schemas import OwnerSchema, OwnerType, RepositorySchema


class TestOwnerSchema:
    """Tests für OwnerSchema."""

    def test_valid_user_owner(self) -> None:
        """Test mit gültigem User-Owner."""
        data = {
            "id": 12345,
            "login": "TestUser",
            "avatar_url": "https://avatars.githubusercontent.com/u/12345",
            "html_url": "https://github.com/testuser",
            "type": "User",
        }
        owner = OwnerSchema(**data)

        assert owner.id == 12345
        assert owner.login == "testuser"  # lowercase normalisiert
        assert owner.type == OwnerType.USER

    def test_valid_org_owner(self) -> None:
        """Test mit gültiger Organization."""
        data = {
            "id": 67890,
            "login": "MyOrg",
            "avatar_url": "https://avatars.githubusercontent.com/u/67890",
            "html_url": "https://github.com/myorg",
            "type": "Organization",
        }
        owner = OwnerSchema(**data)

        assert owner.id == 67890
        assert owner.login == "myorg"  # lowercase normalisiert
        assert owner.type == OwnerType.ORGANIZATION

    def test_login_normalization(self) -> None:
        """Test Login-Normalisierung (lowercase + strip)."""
        data = {
            "id": 1,
            "login": "  TestUser  ",
            "type": "User",
        }
        owner = OwnerSchema(**data)

        assert owner.login == "testuser"

    def test_invalid_url(self) -> None:
        """Test mit ungültiger URL (keine http/https)."""
        data = {
            "id": 1,
            "login": "test",
            "avatar_url": "ftp://invalid.com",  # Kein http/https
            "type": "User",
        }
        owner = OwnerSchema(**data)

        assert owner.avatar_url is None  # Ungültige URL → None


class TestRepositorySchema:
    """Tests für RepositorySchema."""

    def test_valid_repository(self) -> None:
        """Test mit gültigem Repository."""
        data = {
            "id": 123456,
            "node_id": "R_abc123",
            "name": "test-repo",
            "full_name": "testuser/test-repo",
            "private": False,
            "owner": {
                "id": 12345,
                "login": "TestUser",
                "type": "User",
            },
            "description": "A test repository",
            "fork": False,
            "created_at": "2023-11-01T12:34:56Z",
            "updated_at": "2023-11-02T13:45:00Z",
            "stargazers_count": 42,
            "language": "Python",
            "html_url": "https://github.com/testuser/test-repo",
        }

        repo = RepositorySchema(**data)

        assert repo.id == 123456
        assert repo.name == "test-repo"
        assert repo.stargazers_count == 42
        assert repo.owner.login == "testuser"  # lowercase normalisiert
        assert repo.language == "Python"
        assert isinstance(repo.created_at, datetime)

    def test_description_normalization(self) -> None:
        """Test Beschreibungs-Normalisierung."""
        # Leere Beschreibung → None
        data_empty = {
            "id": 1,
            "name": "test",
            "full_name": "user/test",
            "owner": {"id": 1, "login": "user", "type": "User"},
            "description": "   ",
            "created_at": "2023-11-01T12:00:00Z",
            "updated_at": "2023-11-01T12:00:00Z",
        }
        repo = RepositorySchema(**data_empty)
        assert repo.description is None

        # Beschreibung mit Whitespace → trimmed
        data_ws = {
            **data_empty,
            "description": "  Hello World  ",
        }
        repo = RepositorySchema(**data_ws)
        assert repo.description == "Hello World"

    def test_negative_stargazers_rejected(self) -> None:
        """Test dass negative Stargazers einen ValidationError werfen."""
        data = {
            "id": 1,
            "name": "test",
            "full_name": "user/test",
            "owner": {"id": 1, "login": "user", "type": "User"},
            "stargazers_count": -5,  # Ungültig
            "created_at": "2023-11-01T12:00:00Z",
            "updated_at": "2023-11-01T12:00:00Z",
        }
        # Pydantic's ge=0 constraint wirft ValidationError
        with pytest.raises(ValidationError):
            RepositorySchema(**data)

    def test_helper_methods(self) -> None:
        """Test Helper-Methoden."""
        data = {
            "id": 1,
            "name": "test",
            "full_name": "myorg/test",
            "owner": {
                "id": 999,
                "login": "MyOrg",
                "type": "Organization",
            },
            "created_at": "2023-11-01T12:00:00Z",
            "updated_at": "2023-11-01T12:00:00Z",
        }
        repo = RepositorySchema(**data)

        assert repo.get_owner_id() == 999
        assert repo.get_owner_login() == "myorg"
        assert repo.is_organization_owned() is True

    def test_missing_required_fields(self) -> None:
        """Test dass fehlende Pflichtfelder ValidationError werfen."""
        data = {
            "id": 1,
            "name": "test",
            # full_name fehlt → Error
            "owner": {"id": 1, "login": "user", "type": "User"},
        }

        with pytest.raises(ValidationError):
            RepositorySchema(**data)

    def test_extra_fields_ignored(self) -> None:
        """Test dass zusätzliche Felder ignoriert werden."""
        data = {
            "id": 1,
            "name": "test",
            "full_name": "user/test",
            "owner": {"id": 1, "login": "user", "type": "User"},
            "created_at": "2023-11-01T12:00:00Z",
            "updated_at": "2023-11-01T12:00:00Z",
            "unknown_field": "should be ignored",  # Extra
        }

        # Sollte nicht fehlschlagen
        repo = RepositorySchema(**data)
        assert repo.id == 1
