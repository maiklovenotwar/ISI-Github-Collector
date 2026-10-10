"""Performance- und Batching-Hilfen.

Enthält den AdaptiveBatchManager, der die Batch-Größe dynamisch auf Basis
von Ziel-Laufzeiten anpasst (Target-Duration-Ansatz mit AIMD-Strategie).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from threading import Lock
from typing import Optional


@dataclass
class AdaptiveBatchConfig:
    """Konfiguration für den AdaptiveBatchManager."""

    target_duration: float = 5.0  # Ziel-Laufzeit pro Batch in Sekunden
    min_size: int = 50  # Minimale Batch-Größe
    max_size: int = 100  # Maximale Batch-Größe (GitHub per_page Limit)
    # Startwert für die Batch-Größe (kann per BATCH_INITIAL_SIZE überschrieben werden)
    initial_size: int = 100
    increase_step: int = 10  # Additive Erhöhung bei schnellen Batches
    decrease_factor: float = 0.5  # Multiplikativer Faktor bei zu langsamen Batches

    def __post_init__(self) -> None:
        """Wende optionale Umgebungsvariable BATCH_INITIAL_SIZE an.

        Dadurch können wir für Tests eine kleinere Batch-Größe (z.B. 20) erzwingen,
        ohne den Default von 100 zu verändern.
        """

        env_initial = os.getenv("BATCH_INITIAL_SIZE")
        if env_initial:
            try:
                self.initial_size = int(env_initial)
            except ValueError:
                # Ungültiger Wert → Default beibehalten
                pass

        # Stelle sicher, dass min_size nicht größer als initial_size ist
        if self.min_size > self.initial_size:
            self.min_size = self.initial_size


class AdaptiveBatchManager:
    """Verwaltet eine dynamische Batch-Größe basierend auf Laufzeiten.

    Strategie:
    - Wenn ein Batch schneller als `target_duration` ist, wird die Batch-Größe
      *additiv* erhöht (bis `max_size`).
    - Wenn ein Batch langsamer als `target_duration` ist, wird die Batch-Größe
      *multiplikativ* reduziert (nicht unter `min_size`).

    Diese Logik ist bewusst gekapselt, um Orchestrierungsfunktionen
    (z.B. `_process_repositories_batch`) schlank zu halten (C901-Strategie).
    """

    def __init__(self, config: Optional[AdaptiveBatchConfig] = None) -> None:
        self.config = config or AdaptiveBatchConfig()
        self._lock = Lock()
        self.current_size: int = max(
            self.config.min_size,
            min(self.config.initial_size, self.config.max_size),
        )

    def reset(self) -> None:
        """Setze die Batch-Größe auf den Initialwert zurück.

        Wird z.B. beim Start einer neuen Periode verwendet, damit "Strafen"
        aus früheren Perioden (verkleinerte Batches) nicht ungewollt
        übernommen werden.
        """
        with self._lock:
            self.current_size = max(
                self.config.min_size,
                min(self.config.initial_size, self.config.max_size),
            )

    def update(self, duration: float, item_count: int) -> int:
        """Aktualisiere die Batch-Größe basierend auf der letzten Laufzeit.

        Args:
            duration: Gemessene Laufzeit des letzten Batches in Sekunden.
            item_count: Anzahl der im Batch verarbeiteten Elemente.

        Returns:
            Neue Batch-Größe (`current_size`).
        """
        if item_count <= 0 or duration <= 0:
            # Nichts oder unbrauchbare Messung → Größe unverändert lassen
            return self.current_size

        target = self.config.target_duration

        with self._lock:
            if duration < target:
                # Batch war schneller als Ziel → additiv erhöhen
                new_size = self.current_size + self.config.increase_step
            else:
                # Batch war langsamer als Ziel → multiplikativ verringern
                new_size = int(self.current_size * self.config.decrease_factor)

            # Grenzen anwenden
            new_size = max(self.config.min_size, min(new_size, self.config.max_size))
            self.current_size = new_size
            return self.current_size

    def __repr__(self) -> str:  # pragma: no cover - reine Komfort-Funktion
        return (
            f"AdaptiveBatchManager(size={self.current_size}, "
            f"min={self.config.min_size}, max={self.config.max_size}, "
            f"target={self.config.target_duration}s)"
        )
