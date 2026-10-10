"""Tests für Pydantic-Validierung im RepositoryCollector (Phase 3.1)."""

import pytest
from pydantic import ValidationError

from github_collector.schemas import RepositorySchema


class TestRepositoryValidationLogic:
    """Tests für die Pydantic Validierungs-Logik (Schema-Level)."""

    def test_valid_repository_passes_validation(self):
        """Test dass ein valides Repository die Validierung besteht."""
        valid_repo = {
            "id": 123456,
            "name": "test-repo",
            "full_name": "user/test-repo",
            "private": False,
            "owner": {
                "id": 789,
                "login": "testuser",
                "type": "User",
            },
            "description": "A test repository",
            "fork": False,
            "created_at": "2023-11-01T12:34:56Z",
            "updated_at": "2023-11-02T13:45:00Z",
            "stargazers_count": 42,
            "forks_count": 5,
            "watchers_count": 10,
            "language": "Python",
            "html_url": "https://github.com/user/test-repo",
        }

        # Sollte nicht fehlschlagen
        repo = RepositorySchema(**valid_repo)
        assert repo.id == 123456
        assert repo.full_name == "user/test-repo"

    def test_invalid_repository_is_filtered(self):
        """Test dass ein invalides Repository herausgefiltert wird."""
        invalid_repo = {
            "id": 123456,
            "name": "test-repo",
            # full_name fehlt → ValidationError
            "owner": {
                "id": 789,
                "login": "testuser",
                "type": "User",
            },
            "created_at": "2023-11-01T12:34:56Z",
            "updated_at": "2023-11-02T13:45:00Z",
        }

        # Sollte ValidationError werfen
        with pytest.raises(ValidationError) as exc_info:
            RepositorySchema(**invalid_repo)

        assert "full_name" in str(exc_info.value)

    def test_mixed_valid_invalid_repositories(self):
        """Test mit Mix aus validen und invaliden Repositories."""
        repos = [
            # Valid
            {
                "id": 1,
                "name": "valid",
                "full_name": "user/valid",
                "owner": {"id": 1, "login": "user", "type": "User"},
                "created_at": "2023-11-01T12:00:00Z",
                "updated_at": "2023-11-01T12:00:00Z",
            },
            # Invalid (missing full_name)
            {
                "id": 2,
                "name": "invalid",
                "owner": {"id": 1, "login": "user", "type": "User"},
                "created_at": "2023-11-01T12:00:00Z",
                "updated_at": "2023-11-01T12:00:00Z",
            },
            # Valid
            {
                "id": 3,
                "name": "valid2",
                "full_name": "user/valid2",
                "owner": {"id": 1, "login": "user", "type": "User"},
                "created_at": "2023-11-01T12:00:00Z",
                "updated_at": "2023-11-01T12:00:00Z",
            },
        ]

        # Validiere alle Repos
        valid_repos = []
        invalid_count = 0

        for repo in repos:
            try:
                validated = RepositorySchema(**repo)
                valid_repos.append(validated)
            except ValidationError:
                invalid_count += 1

        # 2 valide, 1 invalid
        assert len(valid_repos) == 2
        assert invalid_count == 1
        assert valid_repos[0].id == 1
        assert valid_repos[1].id == 3

    def test_negative_stargazers_rejected(self):
        """Test dass negative Stargazers rejected werden."""
        repo_with_negative_stars = {
            "id": 1,
            "name": "test",
            "full_name": "user/test",
            "owner": {"id": 1, "login": "user", "type": "User"},
            "stargazers_count": -5,  # Invalid
            "created_at": "2023-11-01T12:00:00Z",
            "updated_at": "2023-11-01T12:00:00Z",
        }

        # Sollte ValidationError werfen (ge=0 constraint)
        with pytest.raises(ValidationError) as exc_info:
            RepositorySchema(**repo_with_negative_stars)

        assert "stargazers_count" in str(exc_info.value)
