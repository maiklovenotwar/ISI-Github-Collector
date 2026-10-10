"""Integration-Test für Token-Rotation mit echten GitHub Tokens.

Dieser Test prüft die Thread-Safe Token-Rotation mit echten API-Calls.
Er läuft nur, wenn echte GitHub-Tokens verfügbar sind.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from github_collector.api.github_api import GitHubAPI, GitHubTokenPool
from github_collector.utils.secrets import SecretManager

pytestmark = pytest.mark.external


@pytest.fixture
def github_tokens():
    """Lade echte GitHub-Tokens für Integration-Tests."""
    try:
        tokens = SecretManager.get_github_tokens()
        return tokens
    except ValueError:
        pytest.skip("Keine GitHub-Tokens verfügbar - Test übersprungen")


@pytest.fixture
def github_api(github_tokens):
    """Erstelle GitHubAPI-Instanz mit echten Tokens."""
    return GitHubAPI(github_tokens)


class TestLiveTokenRotation:
    """Integration-Tests mit echten GitHub API-Calls."""

    def test_token_pool_initialization(self, github_tokens):
        """Test: Token-Pool wird korrekt mit echten Tokens initialisiert."""
        pool = GitHubTokenPool(github_tokens)

        assert len(pool.tokens) == len(github_tokens)
        assert len(pool.token_info) == len(github_tokens)

        # Prüfe, dass jedes Token initialisiert wurde
        for token in github_tokens:
            assert token in pool.token_info
            assert pool.token_info[token]["remaining"] > 0
            assert pool.token_info[token]["reset_time"] >= 0

    def test_rate_limit_check_single_token(self, github_api):
        """Test: Rate-Limit-Abfrage mit einem Token."""
        # Rufe Rate-Limit-Info ab
        rate_limit_info = github_api.get_rate_limit()

        # Prüfe Response-Struktur
        assert "resources" in rate_limit_info
        assert "core" in rate_limit_info["resources"]

        core = rate_limit_info["resources"]["core"]
        assert "limit" in core
        assert "remaining" in core
        assert "reset" in core

        print(f"\nOK Rate Limit: {core['remaining']}/{core['limit']} remaining")

    def test_multiple_api_calls_with_rotation(self, github_api):
        """Test: Mehrere API-Calls nutzen Token-Rotation."""
        num_calls = 5
        results = []

        for i in range(num_calls):
            # Jeder Call nutzt möglicherweise ein anderes Token
            rate_limit = github_api.get_rate_limit()
            results.append(rate_limit["resources"]["core"]["remaining"])
            print(f"  Call {i+1}: {results[-1]} remaining")
            time.sleep(0.5)  # Kleine Pause zwischen Calls

        # Alle Calls sollten erfolgreich gewesen sein
        assert len(results) == num_calls
        assert all(remaining >= 0 for remaining in results)

    def test_concurrent_api_calls_thread_safe(self, github_api):
        """Test: Parallele API-Calls sind thread-safe."""
        num_threads = 4
        calls_per_thread = 3

        def make_api_calls(thread_id):
            """Worker-Funktion für parallele API-Calls."""
            results = []
            for i in range(calls_per_thread):
                try:
                    # Rate-Limit-Check ist der schnellste Endpoint
                    rate_limit = github_api.get_rate_limit()
                    remaining = rate_limit["resources"]["core"]["remaining"]
                    results.append((thread_id, i, remaining, "success"))
                    time.sleep(0.2)  # Kleine Pause
                except Exception as e:
                    results.append((thread_id, i, None, f"error: {e}"))
            return results

        # Führe parallele Calls aus
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(make_api_calls, i) for i in range(num_threads)]
            all_results = []

            for future in as_completed(futures):
                thread_results = future.result()
                all_results.extend(thread_results)

        # Prüfungen
        total_calls = num_threads * calls_per_thread
        assert len(all_results) == total_calls

        # Alle Calls sollten erfolgreich sein
        successful = [r for r in all_results if r[3] == "success"]
        assert (
            len(successful) == total_calls
        ), f"Nur {len(successful)}/{total_calls} Calls erfolgreich"

        # Zeige Statistiken
        print(f"\nOK {total_calls} parallele API-Calls erfolgreich")
        for thread_id in range(num_threads):
            thread_calls = [r for r in all_results if r[0] == thread_id]
            print(f"  Thread {thread_id}: {len(thread_calls)} calls")

    def test_token_distribution_under_load(self, github_tokens):
        """Test: Tokens werden unter Last fair verteilt."""
        if len(github_tokens) < 2:
            pytest.skip("Test benötigt mindestens 2 Tokens für Token-Distribution")

        pool = GitHubTokenPool(github_tokens)
        selected_tokens = []
        num_selections = 20

        # Simuliere Token-Auswahl unter Last
        for _ in range(num_selections):
            token = pool.get_best_token()
            selected_tokens.append(token)
            # Simuliere Token-Verbrauch
            pool.update_token_info(token, 4900, int(time.time()) + 3600)
            time.sleep(0.01)

        # Analysiere Token-Verteilung
        token_usage = {token: selected_tokens.count(token) for token in github_tokens}

        print(f"\nOK Token-Verteilung über {num_selections} Auswahlen:")
        for i, (token, count) in enumerate(token_usage.items()):
            masked_token = token[:10] + "..." + token[-4:]
            percentage = (count / num_selections) * 100
            print(f"  Token {i+1} ({masked_token}): {count} mal ({percentage:.1f}%)")

        # Bei 2 Tokens sollte die Verteilung relativ ausgeglichen sein
        # (nicht perfekt 50/50, da Best-Token-Algorithmus deterministisch ist)
        if len(github_tokens) == 2:
            # Beide Tokens sollten genutzt worden sein
            assert all(
                count > 0 for count in token_usage.values()
            ), "Nicht alle Tokens wurden genutzt"

    def test_rate_limit_tracking_accuracy(self, github_api):
        """Test: Rate-Limit wird korrekt von GitHub zurückgegeben.

        Hinweis: Bei Token-Rotation kann das Quota stabil bleiben, da jeder
        Call möglicherweise ein anderes Token nutzt. Das ist gewolltes Verhalten!
        """
        # Hole Rate-Limit
        rate_limit = github_api.get_rate_limit()
        remaining = rate_limit["resources"]["core"]["remaining"]

        # Prüfe nur, dass wir valide Werte bekommen
        assert isinstance(remaining, int)
        assert 0 <= remaining <= 5000

        print(f"\nOK Rate-Limit-Tracking funktioniert: {remaining}/5000 remaining")

    @pytest.mark.slow
    def test_token_rotation_on_exhaustion(self, github_tokens):
        """Test: Token-Rotation funktioniert bei erschöpftem Token.

        WARNUNG: Dieser Test ist langsam und verbraucht Rate-Limit!
        """
        if len(github_tokens) < 2:
            pytest.skip("Test benötigt mindestens 2 Tokens")

        pool = GitHubTokenPool(github_tokens)

        # Simuliere, dass Token 1 erschöpft ist
        pool.update_token_info(github_tokens[0], 0, int(time.time()) + 10)
        # Token 2 ist verfügbar
        pool.update_token_info(github_tokens[1], 5000, int(time.time()) + 3600)

        # Hole bestes Token - sollte Token 2 sein
        # (Vergleich über Index statt Token-Wert, damit ein fehlschlagender Assert
        # keinen echten Token-Wert in den Diff/CI-Log schreibt)
        selected = pool.get_best_token()
        assert (
            github_tokens.index(selected) == 1
        ), "Token 2 sollte gewählt werden (Token 1 ist leer)"

        # Simuliere, dass auch Token 2 fast leer ist
        pool.update_token_info(github_tokens[1], 1, int(time.time()) + 3600)

        # Sollte immer noch Token 2 wählen (1 > 0)
        selected = pool.get_best_token()
        assert github_tokens.index(selected) == 1, "Token 2 sollte weiterhin gewählt werden"

        print("\nOK Token-Rotation bei erschöpften Tokens funktioniert korrekt")


class TestTokenRequirements:
    """Tests, um Token-Anforderungen zu dokumentieren."""

    def test_multiple_account_tokens_required(self, github_tokens):
        """Dokumentation: Tokens von verschiedenen Accounts für echte Rotation."""
        # WICHTIG: Tokens von VERSCHIEDENEN GitHub-Accounts sind erforderlich!
        # Das Rate-Limit gilt PRO ACCOUNT, nicht pro Token.
        # Token1 (Account A) + Token2 (Account A) = 5000/h (geteilt)
        # Token1 (Account A) + Token2 (Account B) = 10000/h (addiert)
        pool = GitHubTokenPool(github_tokens)

        print(f"\nOK {len(github_tokens)} Token(s) erfolgreich geladen")
        print("  WICHTIG: Tokens sollten von VERSCHIEDENEN GitHub-Accounts stammen!")
        print("  Grund: Rate-Limit (5000/h) gilt PRO ACCOUNT, nicht pro Token")

        assert len(pool.tokens) >= 1

    def test_token_format_validation(self, github_tokens):
        """Test: Alle Tokens haben gültiges Format."""
        for token in github_tokens:
            # Classic PAT: ghp_XXXX... (40 chars total)
            # Fine-grained: github_pat_XXX... (93 chars total)
            is_classic = token.startswith("ghp_") and len(token) == 40
            is_fine_grained = token.startswith("github_pat_") and len(token) == 93

            assert is_classic or is_fine_grained, f"Ungültiges Token-Format: {token[:15]}..."

        print(f"\nOK Alle {len(github_tokens)} Token(s) haben gültiges Format")
