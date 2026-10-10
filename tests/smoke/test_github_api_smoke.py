"""Smoke-Tests für die GitHubAPI.

Diese Tests führen echte, nicht schreibende Aufrufe gegen die GitHub API aus,
um sicherzustellen, dass die Integration nach Refactorings weiterhin
funktioniert.

Voraussetzungen:
- GITHUB_API_TOKEN oder GITHUB_API_TOKENS ist gesetzt (z.B. via .env).
- Aufruf mit `pytest tests/smoke/ -m smoke`; im Standardlauf sind diese Tests abgewählt.
"""

import os

import pytest

from github_collector.api.github_api import GitHubAPI

REQUIRES_TOKEN = not (os.getenv("GITHUB_API_TOKEN") or os.getenv("GITHUB_API_TOKENS"))


@pytest.mark.smoke
@pytest.mark.external
@pytest.mark.skipif(
    REQUIRES_TOKEN,
    reason="Kein GitHub API Token konfiguriert (GITHUB_API_TOKEN oder GITHUB_API_TOKENS)",
)
def test_github_api_get_rate_limit_smoke():
    """Ein einfacher Smoke-Test für GitHubAPI.get_rate_limit()."""
    token = os.getenv("GITHUB_API_TOKEN")
    tokens_env = os.getenv("GITHUB_API_TOKENS", "")

    tokens = []
    if tokens_env:
        tokens.extend([t.strip() for t in tokens_env.split(",") if t.strip()])
    if token:
        tokens.insert(0, token)

    api = GitHubAPI(tokens=tokens)

    result = api.get_rate_limit()

    # Grundlegende Plausibilitätschecks
    assert isinstance(result, dict)
    assert "resources" in result
    core = result["resources"].get("core", {})
    assert "limit" in core
    assert "remaining" in core
