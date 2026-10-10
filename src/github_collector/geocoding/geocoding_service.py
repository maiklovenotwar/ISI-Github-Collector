"""Geocoding-Service für die Umwandlung von Standortangaben in Länder- und Regionsinformationen."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

import pycountry

from github_collector.config import CACHE_CONFIG
from github_collector.geocoding.cache import GeocodingCache
from github_collector.geocoding.heuristics import HeuristicResolver
from github_collector.geocoding.nominatim_client import NominatimClient
from github_collector.geocoding.normalization import normalize_location
from github_collector.geocoding.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class GeocodingService:

    def __init__(
        self,
        cache_file: Optional[str] = None,
        user_agent: str = "GitHub-Data-Collector",
        enable_quality_logging: bool = False,
        use_cache: bool = True,
    ):
        self.use_cache = use_cache
        if use_cache:
            if cache_file is None:
                cache_file = str(CACHE_CONFIG["geocoding_file"])
            self.cache = GeocodingCache(cache_file)
        else:
            self.cache = None

        self.nominatim_client = NominatimClient(user_agent=user_agent)
        self.heuristics = HeuristicResolver()
        self.rate_limiter = RateLimiter(min_interval_seconds=1.0)
        self.enable_quality_logging = enable_quality_logging

        # Lade Ländercodes für schnelle Lookups
        self.country_codes = {
            country.name.lower(): country.alpha_2 for country in pycountry.countries
        }
        self.country_codes.update(
            {country.alpha_2.lower(): country.alpha_2 for country in pycountry.countries}
        )
        self.country_codes.update(
            {country.alpha_3.lower(): country.alpha_2 for country in pycountry.countries}
        )

    def geocode(self, location: str) -> Dict[str, Any]:  # noqa: C901
        # 1. Cache-Prüfung (nur wenn Cache aktiviert)
        if self.cache is not None:
            cached_result = self.cache.get(location)
            if cached_result is not None:
                return cached_result

        # 2. Vorverarbeitung & Normalisierung
        processed_location = self._preprocess_location(location)
        normalized = normalize_location(processed_location) if processed_location else ""
        if not processed_location or not normalized:
            if self.cache is not None:
                self.cache.set(location, {})
            return {}

        # 3. Heuristische Mappings (Problemfälle & Aliase)
        mapped = self.heuristics.resolve_mapping(normalized)
        if mapped is not None:
            if self.cache is not None:
                if mapped:
                    self.cache.set(location, mapped)
                else:
                    self.cache.set(location, {})
            return mapped or {}

        # 4. Textbasierte Heuristiken VOR Nominatim (verhindert False Positives)
        code, region = self.heuristics.extract_country_from_text(normalized)
        if code:
            country_name = self._get_country_name(code)
            result = {
                "country_code": code,
                "country": country_name,
                "region": region,
                "latitude": None,
                "longitude": None,
                "formatted_address": country_name,
            }
            if self.enable_quality_logging:
                logger.debug("Heuristic match for '%s': %s", location, result)
            if self.cache is not None:
                self.cache.set(location, result)
            return result

        # 5. Nominatim-Geocoding (nur wenn Heuristiken nichts finden)
        self.rate_limiter.wait_for_api()
        api_result = self.nominatim_client.geocode(processed_location)
        if api_result is not None:
            country_code = api_result.get("country_code", "")
            country_name = api_result.get("country", "")
            if country_code and len(country_code) == 2 and country_name.lower() != "earth":
                region = self._extract_region(api_result.get("address", {}))
                result = {
                    "country_code": country_code,
                    "country": country_name,
                    "region": region,
                    "latitude": api_result.get("latitude"),
                    "longitude": api_result.get("longitude"),
                    "formatted_address": api_result.get("formatted_address"),
                }
                if self.enable_quality_logging:
                    logger.debug("Nominatim successful for '%s': %s", location, result)
                if self.cache is not None:
                    self.cache.set(location, result)
                return result

        # 6. Wenn alles fehlschlägt, gib leeres Ergebnis zurück und cache es
        if self.enable_quality_logging:
            logger.warning(
                "Could not determine valid geocoding data for location: '%s' after "
                "all attempts.",
                location,
            )
        if self.cache is not None:
            self.cache.set(location, {})
        return {}

    def _preprocess_location(self, location: str) -> Optional[str]:
        """
        Normalisiere und bereinige Standortangaben für bessere Erkennung.
        Entfernt nur überflüssige Leerzeichen und gibt None zurück, wenn leer.
        Kann erweitert werden, um häufige Muster zu bereinigen.
        """
        if not location or not isinstance(location, str):
            return None

        location = location.strip()

        if not location:
            return None

        # Entferne gängige, nichtssagende Zusätze (optional, aber kann helfen)
        location = re.sub(
            r"\s*,?\s*(remote|home office|anywhere|on the internet|cloud)\s*$",
            "",
            location,
            flags=re.IGNORECASE,
        ).strip()
        location = re.sub(
            r"^\s*(remote|home office|anywhere|on the internet|cloud)\s*,?\s*",
            "",
            location,
            flags=re.IGNORECASE,
        ).strip()

        # Entferne URLs
        location = re.sub(r"https?://\S+", "", location).strip()

        # Entferne E-Mail-Adressen
        location = re.sub(r"\S+@\S+", "", location).strip()

        # Entferne typische Platzhalter/ungültige Zeichen, die Probleme machen könnten
        location = location.replace("[", "").replace("]", "").replace("(", "").replace(")", "")
        location = location.replace('"', "").replace("'", "")

        # Ersetze mehrere Leerzeichen/Kommas durch ein einzelnes Leerzeichen/Komma
        location = re.sub(r"\s+", " ", location).strip()
        location = re.sub(r",+", ",", location).strip()
        location = re.sub(
            r"\s*?,\s*", ", ", location
        ).strip()  # Einheitliches Format Komma+Leerzeichen

        if not location:  # Erneut prüfen nach Bereinigung
            return None

        return location

    def _extract_country_from_text(self, location: str) -> Tuple[Optional[str], Optional[str]]:
        """Diese Methode wird nun vom HeuristicResolver abgedeckt."""
        return None, None

    def _extract_region(self, address: Dict[str, str]) -> str:
        """
        Extrahiere die Region aus einer Adresse.

        Args:
            address: Adressinformationen von Nominatim

        Returns:
            Regionname oder leerer String, wenn nicht gefunden
        """
        # Prüfe verschiedene Felder, die die Region enthalten könnten
        region_fields = ["state", "province", "county", "region", "state_district", "city"]

        for field in region_fields:
            if field in address and address[field]:
                return address[field]

        return ""

    def _get_country_name(self, country_code: str) -> str:
        """
        Ermittle den Ländernamen aus einem Ländercode.

        Args:
            country_code: ISO-Ländercode (2 Buchstaben)

        Returns:
            Ländername oder leerer String, wenn nicht gefunden
        """
        try:
            country = pycountry.countries.get(alpha_2=country_code)
            return country.name if country else ""
        except Exception:
            return ""

    def update_location_data(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        """
        Aktualisiere die Standortdaten einer Entität (Contributor oder Organisation).

        Args:
            entity: Entitätsdaten mit einem 'location'-Feld

        Returns:
            Aktualisierte Entitätsdaten
        """
        location = entity.get("location")

        if not location:
            return entity

        # Geokodiere die Standortangabe
        geocode_result = self.geocode(location)

        if geocode_result:
            # Aktualisiere die Entitätsdaten
            entity["country_code"] = geocode_result.get("country_code", "")
            entity["region"] = geocode_result.get("region", "")

        return entity
