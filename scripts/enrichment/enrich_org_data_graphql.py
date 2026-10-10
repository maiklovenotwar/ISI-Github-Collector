#!/usr/bin/env python
"""
GraphQL-basierte Organisationsdaten-Qualitätsverbesserung mit lokalem Cache.

Nutzt GitHub GraphQL API für Bulk-Abfragen (100 Orgs pro Call) und
lokalen Cache um redundante API-Calls zu vermeiden.
"""
import argparse
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests
from dateutil import parser as date_parser
from sqlalchemy import bindparam, create_engine, text

# Konfiguriere stdout für UTF-8
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from github_collector.config import DB_URL
from github_collector.utils.secrets import SecretManager

# Logger konfigurieren
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("org_data_graphql.log"),
    ],
)
logger = logging.getLogger(__name__)


class OrganizationCache:
    """Lokaler Cache für Organisationsdaten."""

    def __init__(self, cache_file: str = "org_cache.json"):
        """
        Initialisiere Cache.

        Args:
            cache_file: Pfad zur Cache-Datei
        """
        self.cache_file = Path(cache_file)
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.load_cache()

    def load_cache(self) -> None:
        """Lade Cache von Datei."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self.cache = json.load(f)
                logger.info(f"Cache geladen: {len(self.cache)} Einträge")
            except Exception as e:
                logger.warning(f"Cache konnte nicht geladen werden: {e}")
                self.cache = {}
        else:
            logger.info("Neuer Cache erstellt")

    def save_cache(self) -> None:
        """Speichere Cache in Datei."""
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
            logger.info(f"Cache gespeichert: {len(self.cache)} Einträge")
        except Exception as e:
            logger.error(f"Cache konnte nicht gespeichert werden: {e}")

    def get(self, login: str, max_age_hours: int = 24) -> Optional[Dict[str, Any]]:
        """
        Hole Org-Daten aus Cache falls nicht zu alt.

        Args:
            login: Organization Login
            max_age_hours: Maximales Alter in Stunden

        Returns:
            Org-Daten oder None falls nicht im Cache oder zu alt
        """
        if login not in self.cache:
            return None

        entry = self.cache[login]
        fetched_at = datetime.fromisoformat(entry["fetched_at"])
        max_age = datetime.now() - timedelta(hours=max_age_hours)

        if fetched_at < max_age:
            logger.debug(f"Cache-Eintrag für {login} zu alt")
            return None

        return entry["data"]

    def set(self, login: str, data: Dict[str, Any]) -> None:
        """
        Speichere Org-Daten in Cache.

        Args:
            login: Organization Login
            data: Org-Daten
        """
        self.cache[login] = {
            "data": data,
            "fetched_at": datetime.now().isoformat(),
            "source": "graphql",
        }

    def get_cached_logins(self) -> Set[str]:
        """Hole alle bereits gecachten Logins."""
        return set(self.cache.keys())


class GitHubGraphQLClient:
    """GitHub GraphQL API Client für Bulk-Organization-Abfragen."""

    def __init__(self, token: str):
        """
        Initialisiere GraphQL Client.

        Args:
            token: GitHub API Token
        """
        self.token = token
        self.endpoint = "https://api.github.com/graphql"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )

    def build_organizations_query(self, logins: List[str]) -> str:
        """
        Baue GraphQL Query für mehrere Organizations.

        Args:
            logins: Liste von Organization Logins

        Returns:
            GraphQL Query String
        """
        # Erstelle Aliases für jede Organization
        org_queries = []
        for i, login in enumerate(logins):
            # GraphQL Alias muss mit Buchstabe beginnen
            alias = f"org{i}"
            org_queries.append(
                f"""
                {alias}: organization(login: "{login}") {{
                    login
                    name
                    email
                    websiteUrl
                    location
                    description
                    createdAt
                    updatedAt
                    isVerified
                    twitterUsername
                }}
                """
            )

        query = "query {\n" + "\n".join(org_queries) + "\n}"
        return query

    def fetch_organizations_bulk(self, logins: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        Hole mehrere Organizations in einem GraphQL Call.

        Args:
            logins: Liste von Organization Logins (max 100)

        Returns:
            Dict mit login -> org_data Mapping. Der Wert ist None, wenn GitHub die
            Organisation ausdrücklich nicht kennt. Logins, für die keine verwertbare
            Antwort vorliegt (fehlgeschlagene Anfrage, Antwort ohne Daten), fehlen im
            Dict - sie dürfen nicht als "nicht gefunden" vermerkt werden.
        """
        if len(logins) > 100:
            raise ValueError("Maximal 100 Organizations pro GraphQL Call")

        query = self.build_organizations_query(logins)

        try:
            response = self.session.post(self.endpoint, json={"query": query}, timeout=30)
            response.raise_for_status()

            data = response.json()

            if "errors" in data:
                logger.warning(f"GraphQL Errors: {data['errors']}")

            # Parse Response
            results = {}
            response_data = data.get("data")
            if not isinstance(response_data, dict):
                logger.error("GraphQL-Antwort ohne Daten, Batch wird übersprungen")
                return {}

            # Aliasse, deren Fehler kein schlichtes "nicht gefunden" ist
            unexpected = {
                error["path"][0]
                for error in data.get("errors") or []
                if error.get("type") != "NOT_FOUND" and error.get("path")
            }

            for i, login in enumerate(logins):
                alias = f"org{i}"
                if alias not in response_data or (
                    response_data[alias] is None and alias in unexpected
                ):
                    logger.warning(f"Keine verwertbare Antwort für {login}")
                    continue
                org_data = response_data[alias]

                if org_data:
                    # Konvertiere websiteUrl zu blog für Kompatibilität
                    if "websiteUrl" in org_data:
                        org_data["blog"] = org_data.pop("websiteUrl")

                    # Konvertiere description zu bio für Kompatibilität
                    if "description" in org_data:
                        org_data["bio"] = org_data.pop("description")

                    results[login] = org_data
                else:
                    results[login] = None
                    logger.warning(f"Organization {login} nicht gefunden")

            return results

        except Exception as e:
            logger.error(f"GraphQL Request Fehler: {e}")
            return {}


class GraphQLOrganizationEnricher:
    """GraphQL-basierter Organization Data Quality Enricher."""

    def __init__(
        self,
        token: str,
        cache_file: str = "org_cache.json",
        batch_size: int = 100,
    ):
        """
        Initialisiere Enricher.

        Args:
            token: GitHub API Token
            cache_file: Cache-Datei Pfad
            batch_size: Anzahl Orgs pro GraphQL Call (max 100)
        """
        self.client = GitHubGraphQLClient(token)
        self.cache = OrganizationCache(cache_file)
        self.batch_size = min(batch_size, 100)  # GraphQL Limit
        self.engine = create_engine(DB_URL)

    def get_empty_organizations(
        self, limit: Optional[int] = None, exclude_cached: bool = True
    ) -> List[str]:
        """
        Hole Logins von Organisationen ohne vollständige Daten.

        Args:
            limit: Maximale Anzahl
            exclude_cached: Bereits gecachte Orgs ausschließen

        Returns:
            Liste von Organization Logins
        """
        query = """
            SELECT login FROM organizations
            WHERE name IS NULL AND blog IS NULL AND email IS NULL
            AND location IS NULL AND created_at IS NULL
            AND country_code IS NULL
            AND enrichment_checked_at IS NULL
            ORDER BY id
        """

        if limit:
            query += f" LIMIT {limit}"

        with self.engine.connect() as conn:
            result = conn.execute(text(query)).fetchall()
            logins = [row[0] for row in result]

        # Filtere bereits gecachte Orgs
        if exclude_cached:
            cached_logins = self.cache.get_cached_logins()
            logins = [login for login in logins if login not in cached_logins]
            logger.info(f"Ausgeschlossen: {len(cached_logins)} bereits gecachte Orgs")

        return logins

    def mark_checked(self, logins: List[str]) -> None:
        """
        Vermerke einen Abrufversuch, ohne andere Spalten anzufassen.

        Für Organisationen, die GitHub nicht mehr kennt (gelöscht oder umbenannt) - sie
        würden sonst bei jedem Lauf erneut ausgewählt und abgefragt.

        Args:
            logins: Organization Logins
        """
        if not logins:
            return
        query = text(
            "UPDATE organizations SET enrichment_checked_at = :checked_at WHERE login IN :logins"
        ).bindparams(bindparam("logins", expanding=True))
        try:
            with self.engine.connect() as conn:
                conn.execute(query, {"checked_at": datetime.now(), "logins": logins})
                conn.commit()
        except Exception as e:
            logger.error(f"Fehler beim Vermerken von {len(logins)} nicht gefundenen Orgs: {e}")

    def update_organization_data(self, login: str, api_data: Dict[str, Any]) -> bool:
        """
        Update Organisation mit API-Daten.

        Schreibt nur leere Profilfelder und vermerkt den Abruf in enrichment_checked_at.
        Die orbis_*-Spalten gehören der Orbis-Pipeline und werden nicht angefasst.

        Args:
            login: Organization Login
            api_data: GraphQL API-Daten

        Returns:
            True bei Erfolg
        """
        try:
            # Parse ISO datetime strings
            created_at = None
            updated_at = None

            if api_data.get("createdAt"):
                try:
                    created_at = date_parser.parse(api_data["createdAt"])
                except Exception:
                    logger.warning(f"Konnte createdAt nicht parsen für {login}")

            if api_data.get("updatedAt"):
                try:
                    updated_at = date_parser.parse(api_data["updatedAt"])
                except Exception:
                    logger.warning(f"Konnte updatedAt nicht parsen für {login}")

            update_query = text(
                """
                UPDATE organizations
                SET
                    name = COALESCE(name, :name),
                    email = COALESCE(email, :email),
                    blog = COALESCE(blog, :blog),
                    location = COALESCE(location, :location),
                    bio = COALESCE(bio, :bio),
                    created_at = COALESCE(created_at, :created_at),
                    updated_at = COALESCE(updated_at, :updated_at),
                    enrichment_checked_at = :checked_at
                WHERE login = :login
            """
            )

            with self.engine.connect() as conn:
                conn.execute(
                    update_query,
                    {
                        "login": login,
                        "name": api_data.get("name"),
                        "email": api_data.get("email"),
                        "blog": api_data.get("blog"),
                        "location": api_data.get("location"),
                        "bio": api_data.get("bio"),
                        "created_at": created_at,
                        "updated_at": updated_at,
                        "checked_at": datetime.now(),
                    },
                )
                conn.commit()

            return True

        except Exception as e:
            logger.error(f"Fehler beim Update von {login}: {e}")
            return False

    def process_batch(
        self, logins: List[str], run_id: str, batch_num: int, total_batches: int
    ) -> Dict[str, int]:
        """
        Verarbeite einen Batch von Organisationen mit GraphQL.

        Args:
            logins: Liste von Organization Logins
            run_id: Run ID
            batch_num: Batch-Nummer
            total_batches: Gesamtanzahl Batches

        Returns:
            Statistiken
        """
        stats = {"updated": 0, "errors": 0, "not_found": 0, "cached": 0}

        print(f"\n🔄 Batch {batch_num}/{total_batches}: {len(logins)} Orgs...")

        # Prüfe Cache zuerst
        uncached_logins = []
        for login in logins:
            cached_data = self.cache.get(login)
            if cached_data:
                stats["cached"] += 1
                # Update DB mit gecachten Daten
                if self.update_organization_data(login, cached_data):
                    stats["updated"] += 1
                else:
                    stats["errors"] += 1
            else:
                uncached_logins.append(login)

        if uncached_logins:
            logger.info(f"GraphQL Call für {len(uncached_logins)} neue Orgs")

            # GraphQL Bulk-Abfrage
            bulk_results = self.client.fetch_organizations_bulk(uncached_logins)

            for login, api_data in bulk_results.items():
                if api_data:
                    # Cache speichern
                    self.cache.set(login, api_data)

                    # DB Update
                    if self.update_organization_data(login, api_data):
                        stats["updated"] += 1

                        # Zeige interessante Updates
                        name = api_data.get("name", "N/A")
                        location = api_data.get("location", "N/A")
                        if name != "N/A" or location != "N/A":
                            safe_location = (
                                location[:30] + "..." if len(str(location)) > 30 else location
                            )
                            print(f"    ✅ {login}: {name} ({safe_location})")
                    else:
                        stats["errors"] += 1
                else:
                    stats["not_found"] += 1

            self.mark_checked([login for login, api_data in bulk_results.items() if not api_data])
            # Logins ohne verwertbare Antwort: kein Vermerk, beim nächsten Lauf erneut dran
            stats["errors"] += len(uncached_logins) - len(bulk_results)

        # Cache regelmäßig speichern
        if batch_num % 10 == 0:
            self.cache.save_cache()

        return stats

    def enrich_organizations(
        self, limit: Optional[int] = None, dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Hauptmethode: Enriche Organisationen mit GraphQL.

        Args:
            limit: Maximale Anzahl
            dry_run: Nur Statistiken anzeigen

        Returns:
            Zusammenfassung
        """
        run_id = str(uuid.uuid4())
        start_time = time.time()

        print("=" * 80)
        print("🚀 GRAPHQL ORGANISATIONSDATEN-QUALITÄTSVERBESSERUNG")
        print("=" * 80)
        print(f"Mode: {'DRY-RUN' if dry_run else 'PRODUCTION'}")
        print(f"Run ID: {run_id}")

        # Hole zu verarbeitende Orgs
        logins = self.get_empty_organizations(limit=limit)
        target_count = len(logins)

        print(f"📊 Zu verarbeiten: {target_count:,} Organisationen")
        print(f"📦 Batch-Größe: {self.batch_size} (GraphQL Bulk)")

        if dry_run:
            print("\n📋 Erste 10 Beispiele:")
            for i, login in enumerate(logins[:10], 1):
                print(f"   {i:2d}. {login}")
            return {"dry_run": True, "target_count": target_count}

        # Production Run
        total_stats = {"updated": 0, "errors": 0, "not_found": 0, "cached": 0}
        processed = 0

        # Verarbeite in Batches
        for i in range(0, len(logins), self.batch_size):
            batch_logins = logins[i : i + self.batch_size]
            batch_num = (i // self.batch_size) + 1
            total_batches = (len(logins) + self.batch_size - 1) // self.batch_size

            batch_stats = self.process_batch(batch_logins, run_id, batch_num, total_batches)

            # Update Gesamtstatistiken
            for key, value in batch_stats.items():
                total_stats[key] += value

            processed += len(batch_logins)

            # Progress
            progress_pct = (processed / target_count) * 100
            print(
                f"📈 Progress: {processed:,}/{target_count:,} ({progress_pct:.1f}%) "
                f"- Updated: {total_stats['updated']:,}, "
                f"Cached: {total_stats['cached']:,}"
            )

        # Final Cache Save
        self.cache.save_cache()

        # Zusammenfassung
        duration = time.time() - start_time
        print(f"\n{'='*80}")
        print(f"🎉 FERTIG! Verarbeitet: {processed:,} Orgs in {duration/60:.1f}min")
        print("📊 Statistiken:")
        print(f"   ✅ Aktualisiert: {total_stats['updated']:,}")
        print(f"   💾 Aus Cache: {total_stats['cached']:,}")
        print(f"   ❌ Fehler: {total_stats['errors']:,}")
        print(f"   🔍 Nicht gefunden: {total_stats['not_found']:,}")

        # API-Call-Effizienz
        api_calls = (processed - total_stats["cached"]) // self.batch_size
        print(f"   🚀 API-Calls: {api_calls} (statt {processed:,})")
        print(f"   ⚡ Effizienz: {processed/max(api_calls, 1):.1f}x")
        print(f"{'='*80}")

        return {
            "run_id": run_id,
            "processed": processed,
            "duration": duration,
            "stats": total_stats,
            "api_calls": api_calls,
        }


def main() -> int:
    """Hauptfunktion."""
    parser = argparse.ArgumentParser(description="GraphQL Organisationsdaten-Qualitätsverbesserung")
    parser.add_argument("--dry-run", action="store_true", help="Nur anzeigen, keine Updates")
    parser.add_argument("--limit", type=int, help="Anzahl Organisationen")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Batch-Größe für GraphQL (max 100)",
    )
    parser.add_argument(
        "--cache-file",
        default="org_cache.json",
        help="Cache-Datei Pfad",
    )

    args = parser.parse_args()

    try:
        # GitHub Token
        tokens = SecretManager.get_github_tokens()
        if not tokens:
            print("❌ ERROR: Kein GitHub Token gefunden")
            return 1

        # Verwende ersten Token für GraphQL
        token = tokens[0]
        print("🔑 GitHub Token geladen")

        # Enricher
        enricher = GraphQLOrganizationEnricher(
            token=token,
            cache_file=args.cache_file,
            batch_size=args.batch_size,
        )

        # Verarbeitung
        enricher.enrich_organizations(
            limit=args.limit,
            dry_run=args.dry_run,
        )

        return 0

    except KeyboardInterrupt:
        print("\n⚠️  Unterbrochen durch Benutzer")
        return 1
    except Exception as e:
        logger.error(f"Unerwarteter Fehler: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
