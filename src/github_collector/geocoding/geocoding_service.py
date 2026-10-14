"""Geocoding-Service für die Umwandlung von Standortangaben in Länder- und Regionsinformationen."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import pycountry

from github_collector.config import CACHE_CONFIG
from github_collector.geocoding.cache import GeocodingCache
from github_collector.geocoding.heuristics import HeuristicResolver
from github_collector.geocoding.nominatim_client import (
    GeocodingUnavailableError,
    NominatimClient,
)
from github_collector.geocoding.normalization import normalize_location
from github_collector.geocoding.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


def preprocess_location(location: str) -> Optional[str]:
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
    location = re.sub(r"\s*?,\s*", ", ", location).strip()  # Einheitliches Format Komma+Leerzeichen

    if not location:  # Erneut prüfen nach Bereinigung
        return None

    return location


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
        # Anfragen, auf die der Dienst nicht geantwortet hat (werden nicht gecacht)
        self.unavailable_count = 0
        self.last_request_unanswered = False

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

    def _remember(self, location: str, result: Dict[str, Any], source: str) -> Dict[str, Any]:
        """Lege ein Ergebnis mit Herkunft und Zeitpunkt im Cache ab und gib es zurück.

        Ein Ergebnis ohne Land wird als ``{"source": "no_result", ...}`` gespeichert, an
        den Aufrufer geht dafür weiterhin ein leeres Dict.
        """
        entry = dict(result)
        entry["source"] = source if result else "no_result"
        entry["cached_at"] = datetime.now(timezone.utc).isoformat()
        if self.cache is not None:
            self.cache.set(location, entry)
        return entry if result else {}

    def _mapping_over_cache(self, location: str, mapped: Dict[str, Any]) -> Dict[str, Any]:
        """Gib die Zuordnung zurück; ein Cache-Eintrag mit anderem Land wird ersetzt."""
        if self.cache is not None:
            cached_result = self.cache.get(location)
            if cached_result is not None and cached_result.get("country_code") == mapped.get(
                "country_code"
            ):
                return cached_result
        return self._remember(location, mapped, "mapping")

    def _heuristic_result(self, location: str, normalized: str) -> Optional[Dict[str, Any]]:
        """Ergebnis der Textregeln oder None, wenn sie kein Land bestimmen."""
        code, region = self.heuristics.extract_country_from_text(normalized)
        if not code:
            return None
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
        return result

    def geocode(self, location: str) -> Dict[str, Any]:
        """Bestimme Land und Region für eine Freitext-Ortsangabe.

        Returns:
            Das Ergebnis oder ein leeres Dict, wenn kein Land bestimmt werden konnte. Hat
            der Dienst nicht geantwortet, ist `last_request_unanswered` True; in diesem
            Fall wird nichts gecacht, damit der Ort später erneut angefragt wird.
        """
        self.last_request_unanswered = False

        # 1. Vorverarbeitung & Normalisierung
        processed_location = self._preprocess_location(location)
        normalized = normalize_location(processed_location) if processed_location else ""
        mapped = self.heuristics.resolve_mapping(normalized) if normalized else None

        # 2. Eine kuratierte Zuordnung, die ein Land nennt, ist eine bewusste Korrektur und
        # geht einem älteren Cache-Eintrag vor (Befund K10: "Korea, South" stand als alter
        # Nominatim-Treffer in Australien im Cache). "Kein Ort"-Zuordnungen und Textregeln
        # bleiben hinter dem Cache, dort schützt er oft richtige Werte (z. B. "de" -> DE).
        if mapped:
            return self._mapping_over_cache(location, mapped)

        # 3. Cache-Prüfung (nur wenn Cache aktiviert)
        if self.cache is not None:
            cached_result = self.cache.get(location)
            if cached_result is not None:
                return {} if GeocodingCache.is_empty_result(cached_result) else cached_result

        if not processed_location or not normalized:
            return self._remember(location, {}, "no_result")

        # 4. Übrige Zuordnungen ("kein Ort")
        if mapped is not None:
            return self._remember(location, {}, "mapping")

        # 5. Textbasierte Heuristiken VOR Nominatim (verhindert False Positives)
        heuristic = self._heuristic_result(location, normalized)
        if heuristic:
            return self._remember(location, heuristic, "heuristic")

        # 6. Nominatim-Geocoding (nur wenn Heuristiken nichts finden)
        self.rate_limiter.wait_for_api()
        try:
            api_result = self.nominatim_client.geocode(processed_location)
        except GeocodingUnavailableError as exc:
            # Kein Ergebnis UND keine Aussage über den Ort: nicht cachen.
            self.unavailable_count += 1
            self.last_request_unanswered = True
            logger.warning("Geocoding-Dienst nicht erreichbar für '%s': %s", location, exc)
            return {}

        result = self._result_from_api(api_result)
        if result and self.enable_quality_logging:
            logger.debug("Nominatim successful for '%s': %s", location, result)
        if not result and self.enable_quality_logging:
            logger.warning(
                "Could not determine valid geocoding data for location: '%s' after "
                "all attempts.",
                location,
            )
        return self._remember(location, result, "nominatim")

    def _result_from_api(self, api_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Übersetze eine Nominatim-Antwort; leer, wenn sie kein gültiges Land enthält."""
        if api_result is None:
            return {}
        country_code = api_result.get("country_code", "")
        country_name = api_result.get("country", "")
        if not country_code or len(country_code) != 2 or country_name.lower() == "earth":
            return {}
        return {
            "country_code": country_code,
            "country": country_name,
            "region": self._extract_region(api_result.get("address", {})),
            "latitude": api_result.get("latitude"),
            "longitude": api_result.get("longitude"),
            "formatted_address": api_result.get("formatted_address"),
        }

    def _preprocess_location(self, location: str) -> Optional[str]:
        """Bereinigt eine Standortangabe; siehe preprocess_location."""
        return preprocess_location(location)

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
