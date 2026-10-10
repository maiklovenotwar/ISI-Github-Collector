"""Tests für Thread-Safety des GitHubTokenPool (Phase 1.5)."""

import threading
import time

import pytest

from github_collector.api.github_api import GitHubTokenPool


class TestTokenPoolConcurrency:
    """Tests für Thread-Safety der Token-Pool-Implementierung."""

    @pytest.fixture
    def token_pool(self):
        """Erstelle einen Token-Pool mit zwei Dummy-Tokens."""
        # Verwende realistische Token-Formate (werden nicht validiert in GitHubTokenPool)
        tokens = ["ghp_" + "x" * 36, "ghp_" + "y" * 36]
        return GitHubTokenPool(tokens)

    def test_concurrent_get_best_token(self, token_pool):
        """Test: Mehrere Threads rufen gleichzeitig get_best_token() auf."""
        num_threads = 10
        results = []
        exceptions = []

        def worker():
            try:
                token = token_pool.get_best_token()
                results.append(token)
            except Exception as e:
                exceptions.append(e)

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]

        # Starte alle Threads
        for t in threads:
            t.start()

        # Warte auf Completion
        for t in threads:
            t.join()

        # Prüfungen
        assert len(exceptions) == 0, f"Exceptions occurred: {exceptions}"
        assert len(results) == num_threads
        # Alle results sollten gültige Tokens sein
        assert all(token in token_pool.tokens for token in results)

    def test_concurrent_update_token_info(self, token_pool):
        """Test: Mehrere Threads aktualisieren gleichzeitig Token-Informationen."""
        num_threads = 10
        exceptions = []
        token = token_pool.tokens[0]

        def worker(thread_id):
            try:
                # Jeder Thread updated das Token mit unterschiedlichen Werten
                remaining = 5000 - thread_id * 100
                reset_time = int(time.time()) + thread_id * 60
                token_pool.update_token_info(token, remaining, reset_time)
            except Exception as e:
                exceptions.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]

        # Starte alle Threads
        for t in threads:
            t.start()

        # Warte auf Completion
        for t in threads:
            t.join()

        # Prüfungen
        assert len(exceptions) == 0, f"Exceptions occurred: {exceptions}"
        # Token-Info sollte existieren und valide sein
        assert token in token_pool.token_info
        assert "remaining" in token_pool.token_info[token]
        assert "reset_time" in token_pool.token_info[token]

    def _run_readers(self, token_pool, num_readers, results, exceptions):
        """Helper: Run reader threads."""

        def reader():
            try:
                for _ in range(3):
                    token = token_pool.get_best_token()
                    results.append(("read", token))
                    time.sleep(0.001)
            except Exception as e:
                exceptions.append(("reader", e))

        threads = [threading.Thread(target=reader) for _ in range(num_readers)]
        return threads

    def _run_writers(self, token_pool, num_writers, results, exceptions):
        """Helper: Run writer threads."""

        def writer(thread_id):
            try:
                for i in range(3):
                    token = token_pool.tokens[thread_id % len(token_pool.tokens)]
                    remaining = 5000 - i * 100
                    reset_time = int(time.time()) + i * 60
                    token_pool.update_token_info(token, remaining, reset_time)
                    results.append(("write", token))
                    time.sleep(0.001)
            except Exception as e:
                exceptions.append(("writer", e))

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(num_writers)]
        return threads

    def test_concurrent_mixed_operations(self, token_pool):
        """Test: Mix aus get_best_token() und update_token_info() parallel."""
        results = []
        exceptions = []

        # Erstelle gemischte Threads
        reader_threads = self._run_readers(token_pool, 5, results, exceptions)
        writer_threads = self._run_writers(token_pool, 5, results, exceptions)
        all_threads = reader_threads + writer_threads

        # Starte alle Threads
        for t in all_threads:
            t.start()
        for t in all_threads:
            t.join()

        # Prüfungen
        assert len(exceptions) == 0, f"Exceptions occurred: {exceptions}"
        assert len(results) == 30  # 5 reader * 3 + 5 writer * 3
        # Alle read-results sollten gültige Tokens sein
        for operation, token in results:
            if operation == "read":
                assert token in token_pool.tokens

    def test_concurrent_stress_test(self, token_pool):
        """Stress-Test: Viele Threads, viele Operationen."""
        num_threads = 20
        operations_per_thread = 50
        exceptions = []
        success_count = [0]  # Mutable für Thread-Zugriff

        def worker(thread_id):
            try:
                for i in range(operations_per_thread):
                    if i % 2 == 0:
                        # Read operation
                        token_pool.get_best_token()
                    else:
                        # Write operation
                        token = token_pool.tokens[thread_id % len(token_pool.tokens)]
                        remaining = 5000 - i
                        reset_time = int(time.time()) + i
                        token_pool.update_token_info(token, remaining, reset_time)
                    success_count[0] += 1
            except Exception as e:
                exceptions.append((thread_id, e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]

        # Starte alle Threads
        start_time = time.time()
        for t in threads:
            t.start()

        # Warte auf Completion
        for t in threads:
            t.join()
        duration = time.time() - start_time

        # Prüfungen
        assert len(exceptions) == 0, f"Exceptions occurred: {exceptions}"
        assert success_count[0] == num_threads * operations_per_thread
        print(f"\n✓ Stress test passed: {success_count[0]} operations in {duration:.2f}s")

    def test_no_race_condition_on_token_selection(self, token_pool):
        """Test: Keine Race Condition bei Token-Auswahl unter Last."""
        num_threads = 10
        selected_tokens = []
        lock = threading.Lock()

        # Setze Token-Info so, dass ein Token deutlich besser ist
        token_pool.token_info[token_pool.tokens[0]]["remaining"] = 5000
        token_pool.token_info[token_pool.tokens[1]]["remaining"] = 100

        def worker():
            token = token_pool.get_best_token()
            with lock:
                selected_tokens.append(token)

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Alle Threads sollten das beste Token gewählt haben (Token 0)
        # Da wir Thread-Safety haben, sollte kein Chaos entstehen
        assert len(selected_tokens) == num_threads
        # Das Token mit höherem Quota sollte bevorzugt werden
        assert selected_tokens.count(token_pool.tokens[0]) >= num_threads * 0.5
