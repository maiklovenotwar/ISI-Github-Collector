"""Utilities for normalizing location strings for geocoding.

This module provides a single source of truth for location normalization
used by the geocoding cache and service.
"""

from __future__ import annotations

import re
from typing import Final

from unidecode import unidecode

_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")


def normalize_location(location: str | None) -> str:
    """Normalize a free-text location string.

    The normalization is intentionally conservative:

    - ``None`` or empty/whitespace-only strings become an empty string.
    - Leading and trailing whitespace is stripped.
    - Non-Latin characters are transliterated to ASCII (e.g. "北京" -> "bei jing").
    - The string is lower-cased.
    - Multiple internal whitespace characters are collapsed to a single space.

    Args:
        location: Raw location string from GitHub profiles or organizations.

    Returns:
        A normalized representation suitable for cache keys and heuristic lookups.
    """
    if not location or not isinstance(location, str):
        return ""

    normalized = location.strip()
    if not normalized:
        return ""

    # Transliterate non-Latin characters (CJK, Cyrillic, etc.) to ASCII
    normalized = unidecode(normalized)

    normalized = normalized.lower()
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    return normalized
