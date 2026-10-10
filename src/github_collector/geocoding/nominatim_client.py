from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
from geopy.geocoders import Nominatim

logger = logging.getLogger(__name__)


class NominatimClient:
    def __init__(self, user_agent: str, timeout: int = 10) -> None:
        self._geolocator = Nominatim(user_agent=user_agent)
        self._timeout = timeout

    def geocode(self, location: str) -> Optional[Dict[str, Any]]:
        try:
            geocode_api_result = self._geolocator.geocode(
                location,
                exactly_one=True,
                language="en",
                addressdetails=True,
                timeout=self._timeout,
            )
        except (GeocoderTimedOut, GeocoderUnavailable) as exc:
            logger.warning("Nominatim Geocoding API error for '%s': %s", location, exc)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Unexpected Geocoding exception for '%s': %s", location, exc, exc_info=True
            )
            return None

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
