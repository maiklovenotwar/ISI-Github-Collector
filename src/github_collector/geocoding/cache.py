"""Geocoding cache implementation.

This module provides a JSON-backed cache for geocoding results with
atomic writes to avoid file corruption.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from github_collector.config import CACHE_CONFIG
from github_collector.geocoding.normalization import normalize_location

logger = logging.getLogger(__name__)


class GeocodingCache:
    """Cache for geocoding results.

    The cache is backed by a JSON file on disk. Writes are performed
    atomically via a temporary file and :func:`os.replace` to avoid
    corruption on crashes or interruptions.
    """

    def __init__(self, cache_file: str | Path | None = None) -> None:
        """Initialize the geocoding cache.

        Args:
            cache_file: Optional path to the cache file. If omitted, the
                default from ``CACHE_CONFIG["geocoding_file"]`` is used.
        """
        if cache_file is None:
            path = Path(CACHE_CONFIG["geocoding_file"])
        else:
            path = Path(cache_file)

        self._path: Path = path
        self.cache: Dict[str, Dict[str, Any]] = self._load_cache()

    def _load_cache(self) -> Dict[str, Dict[str, Any]]:
        """Load the cache contents from disk.

        Returns:
            The cached data, or an empty dict if the file does not exist
            or cannot be read.
        """
        if self._path.exists():
            try:
                with self._path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    return data
                logger.warning(
                    "Geocoding-Cache-Datei %s enthält kein Dict, starte mit leerem Cache",
                    self._path,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Fehler beim Laden des Geocoding-Caches aus %s: %s", self._path, exc)
        return {}

    def save(self) -> None:
        """Persist the current cache contents to disk atomically.

        Data is first written to a temporary file in the same directory
        and then moved into place using :func:`os.replace`.
        """
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(self.cache, handle, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self._path)
            logger.debug("Geocoding-Cache atomar gespeichert in %s", self._path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Fehler beim Speichern des Geocoding-Caches in %s: %s", self._path, exc)
            # Best effort: temporäre Datei ggf. entfernen
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:  # noqa: BLE001
                # Im Fehlerfall schweigen, um ursprüngliche Exception nicht zu überdecken
                pass

    def flush(self) -> None:
        """Flush the in-memory cache state to disk.

        This is a thin wrapper around :meth:`save` for semantic clarity.
        """
        self.save()

    @staticmethod
    def is_empty_result(entry: Optional[Dict[str, Any]]) -> bool:
        """Return True for a cached entry that carries no country.

        Covers entries written before metadata was stored (``{}``) as well as
        entries of the form ``{"source": "no_result", ...}``.
        """
        return not entry or not entry.get("country_code")

    def remove(self, location: str) -> bool:
        """Remove the entry for ``location``, if present.

        Returns:
            True if an entry was removed. The cache is not persisted by this
            call; use :meth:`flush`.
        """
        key = normalize_location(location)
        if key and key in self.cache:
            del self.cache[key]
            return True
        return False

    def drop_empty(self) -> int:
        """Remove all entries without a country and persist the cache.

        Returns:
            The number of removed entries.
        """
        empty_keys = [key for key, entry in self.cache.items() if self.is_empty_result(entry)]
        for key in empty_keys:
            del self.cache[key]
        if empty_keys:
            self.save()
        return len(empty_keys)

    def get(self, location: str) -> Optional[Dict[str, Any]]:
        """Return a cached geocoding result for ``location``.

        Args:
            location: Raw location string.

        Returns:
            The cached geocoding result, or ``None`` if the location is
            not present in the cache.
        """
        key = normalize_location(location)
        if not key:
            return None
        return self.cache.get(key)

    def set(self, location: str, result: Dict[str, Any]) -> None:
        """Store a geocoding result in the cache.

        Args:
            location: Raw location string.
            result: Geocoding result to store.
        """
        key = normalize_location(location)
        if not key:
            return

        self.cache[key] = result

        # Persist periodically (after every 10th unique entry) to balance
        # durability and I/O overhead. The exact policy can be tuned if
        # needed, callers may also call :meth:`flush` explicitly.
        if len(self.cache) % 10 == 0:
            self.save()
