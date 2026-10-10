"""Unit-Tests für zentrale Utils (Phase 3.2 - Block 2).

Getestet werden:
- retry_with_backoff (reine Retry-Logik, ohne echte Delays)
- AdaptiveBatchManager (AIMD-Strategie + Thread-Safety-Grundzüge)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from github_collector.utils.performance import AdaptiveBatchConfig, AdaptiveBatchManager
from github_collector.utils.retry import retry_with_backoff


class TestRetryWithBackoffLogic:
    """Tests für den retry_with_backoff-Decorator."""

    @patch("github_collector.utils.retry.time.sleep")
    def test_success_after_single_retry(self, mock_sleep: MagicMock) -> None:
        """Funktion schlägt einmal fehl und succeeds dann (2 Aufrufe insgesamt).

        Erwartung:
        - Decorator versucht den Aufruf erneut.
        - Rückgabewert ist der Erfolgswert des zweiten Aufrufs.
        - time.sleep wird genau einmal aufgerufen (zwischen Versuch 1 und 2).
        """

        call_counter = {"count": 0}

        @retry_with_backoff(max_attempts=3, backoff_factor=2.0, initial_delay=0.5)
        def flaky_function() -> str:
            call_counter["count"] += 1
            if call_counter["count"] == 1:
                raise RuntimeError("transient error")
            return "ok"

        result = flaky_function()

        assert result == "ok"
        assert call_counter["count"] == 2
        mock_sleep.assert_called_once_with(0.5)

    @patch("github_collector.utils.retry.time.sleep")
    def test_fail_after_max_attempts(self, mock_sleep: MagicMock) -> None:
        """Funktion wirft immer eine Exception -> nach max_attempts wird re-raised.

        Erwartung:
        - Die Exception wird nach max_attempts erneut geworfen.
        - Die dekorierte Funktion wurde genau max_attempts Mal aufgerufen.
        - time.sleep wird max_attempts-1 Mal aufgerufen.
        """

        call_counter = {"count": 0}

        @retry_with_backoff(max_attempts=3, backoff_factor=2.0, initial_delay=0.1)
        def always_failing() -> None:
            call_counter["count"] += 1
            raise ValueError("persistent error")

        with pytest.raises(ValueError):
            always_failing()

        assert call_counter["count"] == 3
        # initial_delay=0.1, dann 0.2 – aber wir testen nur, dass zweimal geschlafen wurde
        assert mock_sleep.call_count == 2


class TestAdaptiveBatchManagerLogic:
    """Tests für AdaptiveBatchManager (AIMD-Logik + Thread-Safety-Grundzüge)."""

    @pytest.fixture
    def manager(self) -> AdaptiveBatchManager:
        config = AdaptiveBatchConfig(
            target_duration=5.0,
            min_size=10,
            max_size=500,
            initial_size=100,
            increase_step=10,
            decrease_factor=0.5,
        )
        return AdaptiveBatchManager(config)

    def test_increase_batch_size_for_fast_batches(self, manager: AdaptiveBatchManager) -> None:
        """Bei sehr schnellen Batches soll current_size steigen (> initial_size)."""

        initial = manager.current_size
        # Sehr schnelle Verarbeitung: 0.01s für 100 Items
        new_size = manager.update(duration=0.01, item_count=100)

        assert new_size > initial
        assert manager.current_size == new_size

    def test_decrease_batch_size_for_slow_batches(self, manager: AdaptiveBatchManager) -> None:
        """Bei sehr langsamen Batches soll current_size sinken (< initial_size)."""

        initial = manager.current_size
        # Sehr langsame Verarbeitung: 10s für 100 Items (> target_duration)
        new_size = manager.update(duration=10.0, item_count=100)

        assert new_size < initial
        assert manager.current_size == new_size

    def test_manager_has_internal_lock_for_thread_safety(
        self, manager: AdaptiveBatchManager
    ) -> None:
        """Stelle sicher, dass ein interner Lock für Thread-Safety vorhanden ist.

        Wir prüfen nur die Struktur (Attribut _lock existiert und bietet acquire/release),
        nicht die komplette Parallelitätslogik.
        """

        lock = getattr(manager, "_lock", None)
        assert lock is not None
        # Primitive structural checks – keine echten Threads nötig
        assert hasattr(lock, "acquire")
        assert hasattr(lock, "release")
