"""CLI-Modul für die Aktualisierung der Geocoding-Informationen für Standorte.

Dieses Modul implementiert die Kommandozeilenschnittstelle für die Aktualisierung
der Geocoding-Informationen für Standorte von Contributors und Organisationen.
"""

import argparse
import re
import sys
from datetime import datetime
from typing import Optional

import pycountry

from github_collector import config
from github_collector.database.database import GitHubDatabase
from github_collector.geocoding.geocoding_service import GeocodingService
from github_collector.ui.stats import show_geocoding_stats_after, show_geocoding_stats_before
from github_collector.utils.logging_config import get_geocoding_logger

# Konfiguriere Logging
logger = get_geocoding_logger()

# === Utility Functions and Constants for Robust Geocoding ===
NON_GEO_LOCATIONS = {
    "earth",
    "remote",
    "worldwide",
    "internet",
    "decentralized",
    "nowhere",
    "the moon",
    "metaverse",
    "cyberspace",
    "cloud",
    "virtual",
    "everywhere",
    "anywhere",
}


def is_valid_country_code(code: Optional[str]) -> bool:
    if not code or not isinstance(code, str):
        return False
    if not re.match(r"^[A-Za-z]{2}$", code):
        return False
    try:
        return pycountry.countries.get(alpha_2=code.upper()) is not None
    except Exception as e:
        logger.error(f"Error validating country code '{code}' with pycountry: {e}")
        return False


def is_non_geographic_location(location: str, non_geo_terms: set) -> bool:
    if not location:
        return False
    location_lower = location.lower().strip()
    if location_lower in non_geo_terms:
        return True
    # Check for exact matches in comma/space/pipe separated parts
    parts = set(re.split(r"[\s,|/]+", location_lower))
    return bool(parts.intersection(non_geo_terms))


def prompt_for_date(prompt_text, default=None):
    """Fordert den Benutzer zur Eingabe eines Datums auf."""
    while True:
        if default:
            user_input = input(f"{prompt_text} [Format: YYYY-MM-DD, Standard: {default}]: ").strip()
            if not user_input:
                return default
        else:
            user_input = input(
                f"{prompt_text} [Format: YYYY-MM-DD, leer lassen für keine Einschränkung]: "
            ).strip()
            if not user_input:
                return None

        # Überprüfe, ob das Datum gültig ist
        try:
            datetime.strptime(user_input, "%Y-%m-%d")
            return user_input
        except ValueError:
            print("Ungültiges Datumsformat. Bitte verwenden Sie das Format YYYY-MM-DD.")


def prompt_for_int(prompt_text, default=None):
    """Fordert den Benutzer zur Eingabe einer Ganzzahl auf."""
    while True:
        if default is not None:
            user_input = input(f"{prompt_text} [Standard: {default}]: ").strip()
            if not user_input:
                return default
        else:
            user_input = input(f"{prompt_text}: ").strip()
            if not user_input:
                return None

        try:
            return int(user_input)
        except ValueError:
            print("Ungültige Eingabe. Bitte geben Sie eine Ganzzahl ein.")


def parse_arguments() -> argparse.Namespace:
    """
    Parse Kommandozeilenargumente.

    Returns:
        Geparste Argumente
    """
    parser = argparse.ArgumentParser(
        description="Aktualisiere Geocoding-Informationen für Standorte"
    )

    # Datenbankoptionen
    parser.add_argument("--db-path", help="Pfad zur SQLite-Datenbankdatei")

    # Geocoding-Optionen
    parser.add_argument(
        "--cache-file",
        default=str(config.CACHE_CONFIG["geocoding_file"]),
        help="Pfad zur Geocoding-Cache-Datei (Standard: geocoding_cache.json)",
    )
    parser.add_argument(
        "--user-agent",
        default="GitHub-Data-Collector",
        help="User-Agent für Nominatim (Standard: GitHub-Data-Collector)",
    )

    # Aktualisierungsoptionen
    parser.add_argument(
        "--contributors", action="store_true", help="Nur Contributors aktualisieren"
    )
    parser.add_argument(
        "--organizations", action="store_true", help="Nur Organisationen aktualisieren"
    )

    # Limit-Optionen
    limit_group = parser.add_argument_group("Limit-Optionen")
    limit_group.add_argument(
        "--limit",
        type=int,
        help=(
            "Maximale Anzahl zu aktualisierender Einträge "
            "(wird für beide Typen verwendet, wenn keine spezifischen "
            "Limits angegeben sind)"
        ),
    )

    # Filter-Optionen
    filter_group = parser.add_argument_group("Filter-Optionen")
    filter_group.add_argument(
        "--created-since",
        type=str,
        help=(
            "Nur Entities berücksichtigen, die mindestens ein Repository haben, "
            "das ab diesem Datum erstellt wurde (YYYY-MM-DD)"
        ),
    )
    filter_group.add_argument(
        "--created-until",
        type=str,
        help=(
            "Nur Entities berücksichtigen, die mindestens ein Repository haben, "
            "das vor diesem Datum erstellt wurde (YYYY-MM-DD)"
        ),
    )
    filter_group.add_argument(
        "--min-stars",
        type=int,
        help="Nur Entities mit mindestens dieser Anzahl an Repo-Stars (stars_at_collection)",
    )
    filter_group.add_argument(
        "--max-stars",
        type=int,
        help="Nur Entities mit maximal dieser Anzahl an Repo-Stars (stars_at_collection)",
    )
    filter_group.add_argument(
        "--no-interactive-filters",
        action="store_true",
        help="Deaktiviere interaktive Abfrage von Zeitfenster und Star-Range.",
    )
    limit_group.add_argument(
        "--contributor-limit", type=int, help="Maximale Anzahl zu aktualisierender Contributors"
    )
    limit_group.add_argument(
        "--organization-limit", type=int, help="Maximale Anzahl zu aktualisierender Organisationen"
    )
    limit_group.add_argument(
        "--show-missing",
        action="store_true",
        help=(
            "Zeige nur an, wie viele Einträge ohne Ländercode vorhanden sind, "
            "ohne sie zu aktualisieren"
        ),
    )

    # Automatische Bestätigung
    parser.add_argument(
        "--yes", "-y", action="store_true", help="Automatisch alle Bestätigungen akzeptieren"
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Auch Einträge mit vorhandenen Ländercodes aktualisieren",
    )

    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Leere den Geocoding-Cache vor der Aktualisierung",
    )
    return parser.parse_args()


def _process_single_contributor(
    db: GitHubDatabase,
    geocoder: GeocodingService,
    contributor,
) -> bool:
    """Verarbeite einen einzelnen Contributor.

    Returns:
        True, wenn ein gültiges Geocoding-Ergebnis in der DB gespeichert wurde,
        sonst False.
    """
    if not contributor.location:
        return False

    if is_non_geographic_location(contributor.location, NON_GEO_LOCATIONS):
        logger.info(
            "Überspringe non-geographischen Standort '%s' für Contributor %s",
            contributor.location,
            contributor.login,
        )
        try:
            db.update_contributor(
                contributor,
                {
                    "login": contributor.login,
                    "country_code": "",
                    "country_name": "",
                    "longitude": None,
                    "latitude": None,
                },
            )
        except Exception as db_error:  # noqa: BLE001
            logger.error(
                "Fehler beim Markieren des non-geographischen Contributors %s: %s",
                contributor.login,
                db_error,
            )
        return False

    geocoding_result = geocoder.geocode(contributor.location)

    if geocoding_result:
        country_code = geocoding_result.get("country_code")
        if not is_valid_country_code(country_code):
            logger.warning(
                "Ungültiger Ländercode '%s' für Contributor %s mit Standort '%s'",
                country_code,
                contributor.login,
                contributor.location,
            )
            try:
                db.update_contributor(
                    contributor,
                    {
                        "login": contributor.login,
                        "country_code": "",
                        "country_name": "",
                        "longitude": None,
                        "latitude": None,
                    },
                )
            except Exception as db_error:  # noqa: BLE001
                logger.error(
                    "Fehler beim Markieren des Contributors %s mit ungültigem Ländercode: %s",
                    contributor.login,
                    db_error,
                )
            return False

        update_data = {
            "login": contributor.login,
            "country_code": country_code,
            "country_name": geocoding_result.get("country"),
            "longitude": geocoding_result.get("longitude"),
            "latitude": geocoding_result.get("latitude"),
        }

        try:
            db.update_contributor(contributor, update_data)
            logger.info(
                "Contributor %s aktualisiert: Standort '%s' -> Land '%s' (%s)",
                contributor.login,
                contributor.location,
                geocoding_result.get("country"),
                country_code,
            )
            return True
        except Exception as db_error:  # noqa: BLE001
            logger.error(
                "Fehler beim Aktualisieren des Contributors %s in der Datenbank: %s",
                contributor.login,
                db_error,
            )
            return False

    logger.warning(
        "Kein Geocoding-Ergebnis für Contributor %s mit Standort '%s'",
        contributor.login,
        contributor.location,
    )
    try:
        db.update_contributor(
            contributor,
            {
                "login": contributor.login,
                "country_code": "",
                "country_name": "",
                "longitude": None,
                "latitude": None,
            },
        )
    except Exception as db_error:  # noqa: BLE001
        logger.error(
            "Fehler beim Markieren des Contributors %s ohne Geocoding-Ergebnis: %s",
            contributor.login,
            db_error,
        )
    return False


def update_contributors(
    db: GitHubDatabase,
    geocoder: GeocodingService,
    limit: Optional[int] = None,
    force: bool = False,
    *,
    created_since: Optional[datetime] = None,
    created_until: Optional[datetime] = None,
    min_stars: Optional[int] = None,
    max_stars: Optional[int] = None,
) -> int:
    """Aktualisiere Geocoding-Informationen für Contributors.

    Die Auswahl der zu verarbeitenden Entities erfolgt vollständig über
    ``get_entities_without_location``. Das CLI kümmert sich nur noch um
    Geocoding, Filterung von non-geographischen Standorten und DB-Updates.
    """

    updated_count = 0
    batch_size = 25  # Kleinere Batches für bessere Fehlertoleranz
    batch_index = 0

    while True:
        if limit is not None:
            remaining = limit - updated_count
            if remaining <= 0:
                break
            effective_limit = min(batch_size, remaining)
        else:
            effective_limit = batch_size

        batch_contributors = db.get_entities_for_geocoding(
            "contributor",
            missing_country_only=not force,
            created_from=created_since,
            created_to=created_until,
            min_stars=min_stars,
            max_stars=max_stars,
            limit=effective_limit,
        )
        if not batch_contributors:
            break

        batch_index += 1
        logger.info(
            "Verarbeite Contributor-Batch %s mit %s Einträgen",
            batch_index,
            len(batch_contributors),
        )

        for contributor in batch_contributors:
            try:
                if _process_single_contributor(db, geocoder, contributor):
                    updated_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Fehler bei der Verarbeitung des Contributors %s: %s",
                    getattr(contributor, "login", "<unbekannt>"),
                    exc,
                )

    return updated_count


def _process_single_organization(
    db: GitHubDatabase,
    geocoder: GeocodingService,
    organization,
) -> bool:
    """Verarbeite eine einzelne Organisation.

    Returns:
        True, wenn ein gültiges Geocoding-Ergebnis gespeichert wurde,
        sonst False.
    """
    if not organization.location:
        return False

    if is_non_geographic_location(organization.location, NON_GEO_LOCATIONS):
        logger.info(
            "Überspringe non-geographischen Standort '%s' für Organisation %s",
            organization.location,
            organization.login,
        )
        try:
            db.update_organization(
                organization,
                {
                    "login": organization.login,
                    "country_code": "",
                    "country_name": "",
                    "longitude": None,
                    "latitude": None,
                },
            )
        except Exception as db_error:  # noqa: BLE001
            logger.error(
                "Fehler beim Markieren der Organisation %s als non-geographisch: %s",
                organization.login,
                db_error,
            )
        return False

    geocoding_result = geocoder.geocode(organization.location)

    if geocoding_result:
        country_code = geocoding_result.get("country_code")
        if not is_valid_country_code(country_code):
            logger.warning(
                "Ungültiger Ländercode '%s' für Organisation %s mit Standort '%s'",
                country_code,
                organization.login,
                organization.location,
            )
            try:
                db.update_organization(
                    organization,
                    {
                        "login": organization.login,
                        "country_code": "",
                        "country_name": "",
                        "longitude": None,
                        "latitude": None,
                    },
                )
            except Exception as db_error:  # noqa: BLE001
                logger.error(
                    "Fehler beim Markieren der Organisation %s mit ungültigem Ländercode: %s",
                    organization.login,
                    db_error,
                )
            return False

        update_data = {
            "login": organization.login,
            "country_code": country_code,
            "country_name": geocoding_result.get("country"),
            "longitude": geocoding_result.get("longitude"),
            "latitude": geocoding_result.get("latitude"),
        }

        try:
            db.update_organization(organization, update_data)
            logger.info(
                "Organisation %s aktualisiert: Standort '%s' -> Land '%s' (%s)",
                organization.login,
                organization.location,
                geocoding_result.get("country"),
                country_code,
            )
            return True
        except Exception as db_error:  # noqa: BLE001
            logger.error(
                "Fehler beim Aktualisieren der Organisation %s in der Datenbank: %s",
                organization.login,
                db_error,
            )
            return False

    logger.warning(
        "Kein Geocoding-Ergebnis für Organisation %s mit Standort '%s'",
        organization.login,
        organization.location,
    )
    try:
        db.update_organization(
            organization,
            {
                "login": organization.login,
                "country_code": "",
                "country_name": "",
                "longitude": None,
                "latitude": None,
            },
        )
    except Exception as db_error:  # noqa: BLE001
        logger.error(
            "Fehler beim Markieren der Organisation %s ohne Geocoding-Ergebnis: %s",
            organization.login,
            db_error,
        )
    return False


def update_organizations(
    db: GitHubDatabase,
    geocoder: GeocodingService,
    limit: Optional[int] = None,
    force: bool = False,
    *,
    created_since: Optional[datetime] = None,
    created_until: Optional[datetime] = None,
    min_stars: Optional[int] = None,
    max_stars: Optional[int] = None,
) -> int:
    """Aktualisiere Geocoding-Informationen für Organisationen.

    Analog zu ``update_contributors`` werden nur Entities ohne Country Code
    verarbeitet, und alle Einträge werden nach der Verarbeitung so
    aktualisiert, dass sie nicht erneut von ``get_entities_without_location``
    geliefert werden.
    """

    updated_count = 0
    batch_size = 25
    batch_index = 0

    while True:
        if limit is not None:
            remaining = limit - updated_count
            if remaining <= 0:
                break
            effective_limit = min(batch_size, remaining)
        else:
            effective_limit = batch_size

        batch_organizations = db.get_entities_for_geocoding(
            "organization",
            missing_country_only=not force,
            created_from=created_since,
            created_to=created_until,
            min_stars=min_stars,
            max_stars=max_stars,
            limit=effective_limit,
        )
        if not batch_organizations:
            break

        batch_index += 1
        logger.info(
            "Verarbeite Organisations-Batch %s mit %s Einträgen",
            batch_index,
            len(batch_organizations),
        )

        for organization in batch_organizations:
            try:
                if _process_single_organization(db, geocoder, organization):
                    updated_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Fehler bei der Verarbeitung der Organisation %s: %s",
                    getattr(organization, "login", "<unbekannt>"),
                    exc,
                )

    return updated_count


def count_missing_country_codes(db: GitHubDatabase, entity_type: str) -> int:
    """Zähle Entities mit Location aber ohne Country Code über die DB-Hilfsmethode."""
    entities = db.get_entities_without_location(entity_type, limit=None)
    return len(entities)


def show_missing_country_codes_summary(db: GitHubDatabase):
    """
    Zeigt eine Zusammenfassung der Einträge mit fehlenden Country Codes an.

    Args:
        db: Die Datenbankverbindung
    """
    missing_contributors = count_missing_country_codes(db, "contributor")
    missing_organizations = count_missing_country_codes(db, "organization")

    print("\n===== Zusammenfassung der fehlenden Country Codes =====")
    print(f"Contributors mit Location aber ohne Country Code: {missing_contributors}")
    print(f"Organisationen mit Location aber ohne Country Code: {missing_organizations}")
    print("==================================================\n")


def _initialize_database(args: argparse.Namespace) -> GitHubDatabase:
    """Initialisiere die Datenbankverbindung basierend auf den CLI-Argumenten."""
    db_url = args.db_path or config.DB_URL
    # Falls ein expliziter Pfad übergeben wurde, der keine SQLAlchemy-URL ist, baue eine daraus
    if args.db_path and isinstance(db_url, str) and "://" not in db_url:
        db_url = f"sqlite:///{db_url}"

    logger.info(f"Verbinde mit Datenbank: {db_url}")
    return GitHubDatabase(str(db_url))


def _initialize_geocoder(args: argparse.Namespace) -> GeocodingService:
    """Initialisiere den Geocoding-Service basierend auf den CLI-Argumenten."""
    cache_file = args.cache_file
    user_agent = args.user_agent
    geocoder = GeocodingService(cache_file=cache_file, user_agent=user_agent)
    logger.info(f"Geocoding-Service initialisiert mit Cache-Datei: {cache_file}")
    return geocoder


def _maybe_clear_cache(args: argparse.Namespace, geocoder: GeocodingService) -> None:
    """Leere den Geocoding-Cache optional, falls per CLI angefordert."""
    import os

    if not getattr(args, "clear_cache", False):
        return

    cache_file = args.cache_file
    if os.path.exists(cache_file):
        os.remove(cache_file)
        logger.info(f"Geocoding-Cache '{cache_file}' wurde gelöscht.")

    if hasattr(geocoder, "cache"):
        geocoder.cache.cache = {}
        logger.info("In-Memory-Cache wurde geleert.")


def _confirm_run_and_show_counts(db: GitHubDatabase, args: argparse.Namespace) -> bool:
    """Zeige Zählung der fehlenden Country Codes und frage optional nach Bestätigung."""
    contributor_count = count_missing_country_codes(db, "contributor")
    organization_count = count_missing_country_codes(db, "organization")

    print(
        "\nEs gibt "
        f"{contributor_count} Contributors und "
        f"{organization_count} Organisationen ohne Ländercode."
    )

    # Keine Limits setzen (None = unbegrenzt)
    args.contributor_limit = None
    args.organization_limit = None

    if args.yes:
        return True

    print("\nAlle Einträge ohne Ländercode werden aktualisiert.")
    confirm = input("Möchten Sie fortfahren? (j/n): ").strip().lower()
    if confirm not in {"j", "ja"}:
        print("Vorgang abgebrochen.")
        return False

    return True


def _run_geocoding_updates(  # noqa: C901
    db: GitHubDatabase,
    geocoder: GeocodingService,
    args: argparse.Namespace,
) -> None:
    """Führe die eigentlichen Geocoding-Aktualisierungen aus und zeige Statistiken."""
    # Statistiken vor der Aktualisierung ermitteln
    try:
        contributor_stats_before = db.get_contributor_location_stats()
        org_stats_before = db.get_organization_location_stats()
        show_geocoding_stats_before(contributor_stats_before, org_stats_before)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Fehler beim Ermitteln der Statistiken vor der Aktualisierung: %s",
            exc,
        )

    contributor_limit = args.contributor_limit or args.limit
    organization_limit = args.organization_limit or args.limit

    # Interaktive Abfrage von Repo-Erstellungszeitfenster und Star-Range,
    # sofern nicht explizit deaktiviert oder bereits per CLI-Argument gesetzt.
    if not getattr(args, "no_interactive_filters", False):
        if not args.created_since or not args.created_until:
            print("\nOptional: Repositories nach Erstellungsdatum einschränken.")
            use_date_filter = input("Repo-Erstellungszeitfenster setzen? (J/n): ").strip().lower()
            if use_date_filter in {"", "j", "ja", "y", "yes"}:
                since_str = prompt_for_date("created_since")
                until_str = prompt_for_date("created_until")
                args.created_since = since_str
                args.created_until = until_str

        if args.min_stars is None and args.max_stars is None:
            print("\nOptional: Nach Star-Range filtern (stars_at_collection).")
            use_star_filter = input("Star-Range setzen? (J/n): ").strip().lower()
            if use_star_filter in {"", "j", "ja", "y", "yes"}:
                min_stars = prompt_for_int("min_stars (leer = kein Minimum)")
                max_stars = prompt_for_int("max_stars (leer = kein Maximum)")
                args.min_stars = min_stars
                args.max_stars = max_stars

    updated_contributors = 0
    updated_organizations = 0

    # Parsed Filter in echte Datumswerte umwandeln (Repo-Erstellungszeit)
    created_since_dt: Optional[datetime] = None
    created_until_dt: Optional[datetime] = None
    if args.created_since:
        created_since_dt = datetime.strptime(args.created_since, "%Y-%m-%d")
    if args.created_until:
        created_until_dt = datetime.strptime(args.created_until, "%Y-%m-%d")

    if args.contributors or not args.organizations:
        try:
            logger.info("Starte die Aktualisierung der Contributors...")
            updated_contributors = update_contributors(
                db,
                geocoder,
                limit=contributor_limit,
                force=args.force,
                created_since=created_since_dt,
                created_until=created_until_dt,
                min_stars=args.min_stars,
                max_stars=args.max_stars,
            )
            logger.info("%s Contributors aktualisiert", updated_contributors)
        except Exception as exc:  # noqa: BLE001
            logger.error("Fehler bei der Aktualisierung der Contributors: %s", exc)

    if args.organizations or not args.contributors:
        try:
            logger.info("Starte die Aktualisierung der Organisationen...")
            updated_organizations = update_organizations(
                db,
                geocoder,
                limit=organization_limit,
                force=args.force,
                created_since=created_since_dt,
                created_until=created_until_dt,
                min_stars=args.min_stars,
                max_stars=args.max_stars,
            )
            logger.info("%s Organisationen aktualisiert", updated_organizations)
        except Exception as exc:  # noqa: BLE001
            logger.error("Fehler bei der Aktualisierung der Organisationen: %s", exc)

    try:
        contributor_stats_after = db.get_contributor_location_stats()
        org_stats_after = db.get_organization_location_stats()
        show_geocoding_stats_after(
            contributor_stats_after,
            org_stats_after,
            updated_contributors,
            updated_organizations,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Fehler beim Ermitteln der Statistiken nach der Aktualisierung: %s",
            exc,
        )

    logger.info("Geocoding-Prozess erfolgreich abgeschlossen.")


def main() -> int:
    """Hauptfunktion für die Aktualisierung der Geocoding-Informationen."""
    try:
        args = parse_arguments()
        db = _initialize_database(args)
        geocoder = _initialize_geocoder(args)

        _maybe_clear_cache(args, geocoder)

        # Zeige Zusammenfassung der fehlenden Country Codes
        show_missing_country_codes_summary(db)

        # Wenn nur die Anzeige gewünscht ist, beende hier
        if args.show_missing:
            logger.info("Anzeige der fehlenden Country Codes abgeschlossen.")
            return 0

        if not _confirm_run_and_show_counts(db, args):
            return 0

        _run_geocoding_updates(db, geocoder, args)
        return 0

    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Kritischer Fehler bei der Aktualisierung der Geocoding-Informationen: %s",
            exc,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
