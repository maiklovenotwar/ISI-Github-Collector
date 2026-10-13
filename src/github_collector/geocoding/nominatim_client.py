from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from geopy.geocoders import Nominatim

logger = logging.getLogger(__name__)

# Pause vor dem einen Wiederholungsversuch, wenn der Dienst nicht geantwortet hat
RETRY_DELAY_SECONDS = 5


class GeocodingUnavailableError(Exception):
    """Der Geocoding-Dienst hat nicht geantwortet (Timeout, Limit, Dienstfehler).

    Bewusst von "Dienst hat geantwortet, kein Treffer" (Rückgabe None) getrennt: ein
    Ausfall sagt nichts über den Ort aus und darf nicht als Ergebnis gespeichert werden.
    """


class NominatimClient:
    def __init__(self, user_agent: str, timeout: int = 10) -> None:
        self._geolocator = Nominatim(user_agent=user_agent)
        self._timeout = timeout

    def geocode(self, location: str) -> Optional[Dict[str, Any]]:
        """Frage Nominatim nach einem Ort.

        Returns:
            Das Ergebnis oder None, wenn der Dienst geantwortet, aber nichts gefunden hat.

        Raises:
            GeocodingUnavailableError: Wenn der Dienst auch beim zweiten Versuch nicht
                geantwortet hat. Jede Ausnahme des Aufrufs zählt als "nicht geantwortet".
        """
        geocode_api_result = None
        for attempt in (1, 2):
            try:
                geocode_api_result = self._geolocator.geocode(
                    location,
                    exactly_one=True,
                    language="en",
                    addressdetails=True,
                    timeout=self._timeout,
                )
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Nominatim hat für '%s' nicht geantwortet (Versuch %d/2): %s",
                    location,
                    attempt,
                    exc,
                )
                if attempt == 2:
                    raise GeocodingUnavailableError(str(exc)) from exc
                time.sleep(RETRY_DELAY_SECONDS)

        if not geocode_api_result or not hasattr(geocode_api_result, "raw"):
            return None

        raw = geocode_api_result.raw
        if not isinstance(raw, dict):
            return None

        address = raw.get("address", {}) or {}
        if not isinstance(address, dict):
            address = {}

        return {
            "address": address,
            "country_code": address.get("country_code", "").upper(),
            "country": address.get("country", ""),
            "latitude": getattr(geocode_api_result, "latitude", None),
            "longitude": getattr(geocode_api_result, "longitude", None),
            "formatted_address": getattr(geocode_api_result, "address", None),
        }
