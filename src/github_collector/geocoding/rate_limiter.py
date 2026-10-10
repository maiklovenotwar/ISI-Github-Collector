"""Einfacher Rate Limiter für externe Geocoding-API-Aufrufe.

Der Limiter stellt sicher, dass zwischen zwei API-Aufrufen
mindestens ``min_interval_seconds`` Zeit vergangen ist.
"""

from __future__ import annotations

import time
from typing import Optional


class RateLimiter:
    def __init__(self, min_interval_seconds: float = 1.0) -> None:
        self.min_interval_seconds = float(min_interval_seconds)
        self._last_call_time: Optional[float] = None

    def wait_for_api(self) -> None:
        """Blockiere so lange, bis der Mindestabstand seit dem letzten Call erreicht ist."""
        now = time.monotonic()
        if self._last_call_time is None:
            self._last_call_time = now
            return

        elapsed = now - self._last_call_time
        remaining = self.min_interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_call_time = time.monotonic()
