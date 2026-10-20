"""Oracle Orbis Client für Organization-Enrichment.

Dieses Modul stellt einen Client für die Verbindung zur Oracle Orbis-Datenbank
bereit. Es unterstützt Batch-Queries für effizientes Domain-Matching gegen
die CONTACT_INFO-Tabelle mit ~400 Millionen Einträgen.

Verbindung erfolgt im Thin-Mode (kein Oracle Client erforderlich).

Architektur-Entscheidungen:
    - LAZY CONNECT: Verbindung wird erst beim ersten Query aufgebaut, nicht bei
      Instanziierung. Ermöglicht Nutzung ohne Netzwerk (z.B. für Tests, Dry-Run).
    - INJIZIERBARE CONNECTION: Für Tests kann eine Mock-Connection übergeben werden.
    - MEHRFACH-TREFFER-STRATEGIE: Bei mehreren Orbis-Einträgen für eine Domain
      wird der ERSTE Treffer verwendet (sortiert nach BVD_ID). Ein Warning wird
      geloggt. Begründung: Konsistentes, reproduzierbares Verhalten.
    - CONSECUTIVE ERROR LIMIT: Nach 3 aufeinanderfolgenden Batch-Fehlern wird
      der Lauf abgebrochen (OrbisQueryError). Verhindert stilles Durchlaufen
      bei systematischen Problemen.
"""

import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Protocol

if TYPE_CHECKING:
    pass

from github_collector.config import ORBIS_CONFIG

logger = logging.getLogger(__name__)

# Maximum consecutive batch errors before aborting
MAX_CONSECUTIVE_ERRORS = 3

# Oracle-Hardlimit für Elemente in einer IN-Clause (ORA-01795).
ORACLE_MAX_IN_LIST_SIZE = 1000

# Pro Domain werden 12 URL-Varianten (mit/ohne www., http/https, mit/ohne
# Slash) in dieselbe IN-Clause aufgenommen (siehe _execute_website_batch_query/
# _execute_all_candidates_query). Der generische ORBIS_CONFIG["batch_size"]
# (Default 500, für die anderen Batch-Queries mit 1 Wert pro Item gedacht)
# ist dafür 6x zu groß und wurde bei 500 Organisationen (443 Domains in einem
# einzigen Batch, 443*12=5316 Bindevariablen) tatsächlich mit ORA-01795
# abgewiesen - der gesamte Domain-Match-Batch schlug fehl und fiel komplett
# und unbemerkt auf den schwächeren Namens-Fallback zurück. Domain-Batches
# müssen deshalb unabhängig vom konfigurierten batch_size auf einen Wert
# begrenzt werden, der mit der 12x-Multiplikation unter dem Oracle-Limit bleibt.
URL_VARIANTS_PER_DOMAIN = 12
MAX_DOMAINS_PER_BATCH = ORACLE_MAX_IN_LIST_SIZE // URL_VARIANTS_PER_DOMAIN

# Name der zweiten, lateinischen Namensspalte von CONTACT_INFO. Steht bewusst nicht im
# Repository (Orbis ist lizenziert, keine Schema-Details, E28), sondern nur lokal in der
# Umgebung bzw. `.env`. Ohne Wert liest die Domain-Abfrage dieselben sechs Spalten wie
# bisher, und der Namensvergleich verhält sich wie vor E28.
LATIN_NAME_COLUMN_ENV = "ORBIS_LATIN_NAME_COLUMN"
_ORACLE_COLUMN_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_BASE_CANDIDATE_COLUMNS = 6


def latin_name_column() -> Optional[str]:
    """Name der lateinischen Namensspalte aus `ORBIS_LATIN_NAME_COLUMN`, sonst None.

    Wird bei jedem Aufruf gelesen. Der Wert wird in SQL eingesetzt und muss deshalb ein
    einfacher Oracle-Bezeichner in Großbuchstaben sein.

    Raises:
        ValueError: Wenn der Wert kein solcher Bezeichner ist.
    """
    value = os.environ.get(LATIN_NAME_COLUMN_ENV, "").strip()
    if not value:
        return None
    if not _ORACLE_COLUMN_NAME.fullmatch(value):
        raise ValueError(f"{LATIN_NAME_COLUMN_ENV} ist kein gültiger Spaltenname")
    return value


# Maximale Anzahl Kandidaten, die lookup_batch_by_websites_all_candidates()
# pro Domain zurückgibt (Schutz gegen Speicher-Explosion bei extrem
# domain-mehrfachgenutzten Fällen wie github.com mit ~89 Treffern).
# WICHTIG: 200 war zu niedrig - bei den größten Multis (ibm.com hat >200
# Landesgesellschaften mit derselben Website) hat der bisherige Wert die
# korrekte Konzernmutter-BVDID abgeschnitten, bevor die Scoring-/HQ-Signale
# sie je gesehen haben (empirisch verifiziert: XX000017, IBMs echte
# Konzernmutter, sortiert alphabetisch hinter Position 200 und fehlte
# dadurch komplett im Kandidaten-Pool). Ein paar tausend kurze Dict-Zeilen
# pro Domain sind speichertechnisch trivial - 2000 gibt deutlich mehr
# Headroom, ohne das Schutznetz ganz zu entfernen.
MAX_CANDIDATES_PER_DOMAIN = 2000

# Bekannte zweiteilige Public-Suffixe (Länder-TLDs mit eigenem
# Registrierungs-Level wie "co.uk" oder "com.br"). Fund aus Charge 11
# (Orbis-Notizen (intern): batches/batch11_review.md) und dem
# anschließenden Hold-out-Test: die naive "letzte 2 Labels"-Heuristik
# behandelte "warren.com.br" bzw. "opensource.gammasoft.com.br" fälschlich
# so, als wäre "warren"/"gammasoft" eine Subdomain von "com.br" - dabei IST
# "com.br" selbst der zweiteilige Public Suffix, die echte Root-Domain ist
# "gammasoft.com.br" (3 Labels), nicht "com.br" (2 Labels). Ergebnis: die
# Root-Domain-Fallback-Suche fragte Orbis nach der GENERISCHEN
# Länder-Suffix-Domain statt der spezifischen Firmen-Domain und lieferte
# komplett unbeteiligte Treffer (im Hold-out-Test durch den
# Namens-Plausibilitätsfilter zwar abgefangen, aber die Ursache blieb).
#
# KEINE echte Public-Suffix-Liste (siehe https://publicsuffix.org/) - nur
# eine praktische, nicht erschöpfende Auswahl der häufigsten Fälle, um die
# konkret beobachteten Fehler zu beheben. Bewusst ohne neue Abhängigkeit
# (analog zur difflib-statt-fuzzywuzzy-Entscheidung) - kann bei Bedarf um
# weitere Länder-Suffixe ergänzt werden.
KNOWN_COMPOUND_SUFFIXES = {
    "co.uk",
    "org.uk",
    "net.uk",
    "ac.uk",
    "gov.uk",
    "co.nz",
    "org.nz",
    "net.nz",
    "co.za",
    "org.za",
    "co.jp",
    "or.jp",
    "ne.jp",
    "ac.jp",
    "go.jp",
    "co.kr",
    "or.kr",
    "ne.kr",
    "co.in",
    "org.in",
    "net.in",
    "gen.in",
    "firm.in",
    "ind.in",
    "co.id",
    "or.id",
    "co.th",
    "or.th",
    "ac.th",
    "co.il",
    "org.il",
    "net.il",
    "com.au",
    "net.au",
    "org.au",
    "edu.au",
    "gov.au",
    "com.br",
    "net.br",
    "org.br",
    "com.cn",
    "net.cn",
    "org.cn",
    "com.mx",
    "org.mx",
    "net.mx",
    "com.ar",
    "org.ar",
    "net.ar",
    "com.co",
    "net.co",
    "org.co",
    "com.vn",
    "net.vn",
    "org.vn",
    "edu.vn",
    "com.sg",
    "net.sg",
    "org.sg",
    "edu.sg",
    "com.tw",
    "org.tw",
    "net.tw",
    "com.hk",
    "org.hk",
    "net.hk",
    "edu.hk",
    "com.my",
    "net.my",
    "org.my",
    "com.ph",
    "net.ph",
    "org.ph",
    "com.tr",
    "net.tr",
    "org.tr",
    "com.ua",
    "net.ua",
    "org.ua",
    "com.pk",
    "com.eg",
    "com.sa",
    "com.ng",
    "org.ng",
    "net.ng",
}


def extract_root_domain(domain: str) -> Optional[str]:
    """Extrahiere die Root-Domain aus einer (Sub-)Domain.

    Fund aus Charge 01 (Orbis-Notizen (intern): batches/
    batch01_review.md): viele GH-Orgs verlinken auf eine Subdomain wie
    `opensource.sap.com` statt der echten Firmen-Root-Domain `sap.com` -
    eine reine Domain-Suche auf die Subdomain findet dann nichts, obwohl die
    Firma trivial in Orbis steht.

    Bekannte Einschränkung: nutzt `KNOWN_COMPOUND_SUFFIXES` (siehe oben)
    statt einer echten Public-Suffix-Liste - deckt die häufigsten
    zweiteiligen Länder-TLDs ab, ist aber nicht erschöpfend. Für den
    aktuellen Zweck (Root-Domain als zusätzlicher Suchversuch, nicht als
    einzige Quelle) ist das ein akzeptabler Kompromiss.

    Args:
        domain: Normalisierte Domain (lowercase, ohne Schema/www.).

    Returns:
        Die Root-Domain, oder None wenn `domain` bereits die minimale
        registrierbare Domain ist (kein weiteres Kürzen möglich/nötig).
    """
    if not domain:
        return None
    labels = domain.split(".")
    if len(labels) <= 2:
        return None

    last_two = ".".join(labels[-2:])
    if last_two in KNOWN_COMPOUND_SUFFIXES:
        # Zweiteiliger Suffix (z.B. "com.br"): die registrierbare Domain
        # braucht 3 Labels ("gammasoft.com.br"), nicht 2 ("com.br").
        if len(labels) <= 3:
            return None
        return ".".join(labels[-3:])

    return last_two


def load_blog_blacklist(path: Optional[Path] = None) -> set[str]:
    """Lade die Blog-Domain-Blacklist (exakte Domains und Wildcard-Suffixe).

    Unterstützt neben exakten Domains (`github.com`) auch Wildcard-Suffix-
    Muster (`*.github.io`), um ganze Plattform-Domain-Familien abzudecken -
    Fund aus Charge 01: `*.github.io`-Subdomains sind strukturell nie eine
    echte Firmen-Website (GitHub Pages), das muss nicht einzeln abgefragt
    werden. Siehe Orbis-Notizen (intern): batches/batch01_review.md.

    Args:
        path: Pfad zur Blacklist-Datei. Falls None, wird
            `config/blog_blacklist.txt` relativ zum Projekt-Root verwendet.

    Returns:
        Set aus Zeilen der Datei (exakte Domains und `*.`-Wildcard-Muster),
        Kommentarzeilen (`#`) und Leerzeilen sind ausgeschlossen. Leeres Set
        falls die Datei nicht existiert.
    """
    if path is None:
        path = Path(__file__).parent.parent.parent.parent / "config" / "blog_blacklist.txt"

    if not path.exists():
        return set()

    entries = set()
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip().lower()
            if line and not line.startswith("#"):
                entries.add(line)
    return entries


def is_domain_blacklisted(domain: str, blacklist: set[str]) -> bool:
    """Prüfe, ob eine Domain auf der Blacklist steht (exakt oder per Wildcard).

    Args:
        domain: Normalisierte Domain (lowercase).
        blacklist: Ergebnis von `load_blog_blacklist()`.

    Returns:
        True wenn die Domain exakt gelistet ist, oder ein Wildcard-Suffix-
        Muster (`*.github.io`) auf sie passt.
    """
    if not domain or not blacklist:
        return False
    if domain in blacklist:
        return True
    for pattern in blacklist:
        if pattern.startswith("*.") and domain.endswith(pattern[1:]):
            return True
    return False


class OrbisConnectionError(Exception):
    """Fehler bei der Verbindung zur Orbis-Datenbank."""

    pass


class OrbisQueryError(Exception):
    """Fehler bei der Ausführung einer Orbis-Query."""

    pass


class CursorProtocol(Protocol):
    """Protocol für Cursor-ähnliche Objekte (für Mocking)."""

    def execute(self, query: str, params: Optional[dict] = None) -> None: ...

    def fetchone(self) -> Optional[tuple]: ...

    def fetchall(self) -> list[tuple]: ...

    def __iter__(self) -> "CursorProtocol": ...

    def __next__(self) -> tuple: ...


class ConnectionProtocol(Protocol):
    """Protocol für Connection-ähnliche Objekte (für Mocking)."""

    def cursor(self) -> CursorProtocol: ...

    def ping(self) -> None: ...

    def close(self) -> None: ...


class OrbisClient:
    """Client für Oracle Orbis-Datenbankabfragen.

    Unterstützt Batch-Queries für Domain-Matching mit IN-Clause Optimierung.
    Die Verbindung wird LAZY aufgebaut (erst beim ersten Query).

    Attributes:
        config: Konfigurationsdictionary mit Verbindungsparametern.
        connection: Aktive Oracle-Datenbankverbindung (oder Mock).
        consecutive_errors: Zähler für aufeinanderfolgende Batch-Fehler.

    Example:
        >>> client = OrbisClient()
        >>> results = client.lookup_batch_by_websites(["acme.com", "example.org"])
        >>> client.close()

    Example (mit Mock für Tests):
        >>> fake_conn = FakeConnection(mock_data)
        >>> client = OrbisClient(connection=fake_conn)
        >>> results = client.lookup_batch_by_websites(["acme.com"])
    """

    def __init__(
        self,
        config: Optional[dict[str, Any]] = None,
        connection: Optional[ConnectionProtocol] = None,
    ) -> None:
        """Initialisiert den Orbis-Client.

        LAZY CONNECT: Verbindung wird NICHT sofort aufgebaut, sondern erst
        beim ersten Query. Ermöglicht Instanziierung ohne Netzwerkzugang.

        Args:
            config: Optionales Konfigurationsdictionary. Falls None, wird
                    ORBIS_CONFIG aus config.py verwendet.
            connection: Optionale injizierte Connection (für Tests).
                        Falls None, wird bei Bedarf eine echte Oracle-
                        Verbindung aufgebaut.

        Raises:
            ValueError: Wenn erforderliche Konfigurationsparameter fehlen
                        UND keine Connection injiziert wurde.
        """
        self.config = config or ORBIS_CONFIG
        self._injected_connection = connection is not None
        self.connection: Optional[ConnectionProtocol] = connection
        self.consecutive_errors: int = 0

        # Config nur validieren wenn keine Connection injiziert wurde
        if not self._injected_connection:
            self._validate_config()

    def _validate_config(self) -> None:
        """Validiert die Konfigurationsparameter.

        Raises:
            ValueError: Wenn erforderliche Parameter fehlen oder leer sind.
        """
        required_fields = ["host", "port", "service", "user", "password"]
        missing = [f for f in required_fields if not self.config.get(f)]

        if missing:
            raise ValueError(
                f"Fehlende Orbis-Konfiguration: {', '.join(missing)}. "
                "Bitte .env Datei prüfen (ORBIS_HOST, ORBIS_PORT, etc.)"
            )

    def _connect(self) -> None:
        """Stellt die Verbindung zur Oracle-Datenbank her (LAZY).

        Verwendet den Thin-Mode von oracledb (kein Oracle Instant Client nötig).
        Wird nur aufgerufen wenn keine Connection injiziert wurde.

        Raises:
            OrbisConnectionError: Bei Verbindungsfehlern.
        """
        # Import hier um Circular Dependencies zu vermeiden und
        # um Import-Fehler bei fehlendem oracledb zu verzögern
        try:
            import oracledb
        except ImportError as e:
            raise OrbisConnectionError(
                "oracledb Paket nicht installiert. Bitte 'pip install oracledb' ausführen."
            ) from e

        dsn = f"{self.config['host']}:{self.config['port']}/{self.config['service']}"

        logger.info("Verbinde zu Oracle Orbis: %s", dsn)

        try:
            self.connection = oracledb.connect(
                user=self.config["user"],
                password=self.config["password"],
                dsn=dsn,
            )
            # WICHTIG (Fund beim 1000-Organisationen-Dry-Run): ohne
            # call_timeout kann ein einzelner Query-Aufruf unbegrenzt
            # hängen bleiben (z.B. bei einem stillen Netzwerk-Abbruch ohne
            # TCP-RST) - im konkreten Fall 42+ Minuten Stillstand ohne jede
            # Fehlermeldung, mitten im Domain-Batch-Loop. call_timeout (in
            # Millisekunden) sorgt dafür, dass ein hängender Aufruf nach
            # spätestens 3 Minuten mit einem oracledb.Error abbricht - das
            # greift dann automatisch in die bestehende
            # MAX_CONSECUTIVE_ERRORS-Behandlung der Batch-Loops.
            self.connection.call_timeout = 180000
            logger.info("Oracle Orbis Verbindung hergestellt")
        except oracledb.Error as e:
            error_msg = f"Verbindung zu Oracle Orbis fehlgeschlagen: {e}"
            logger.error(error_msg)
            raise OrbisConnectionError(error_msg) from e

    def _ensure_connection(self) -> None:
        """Stellt sicher, dass eine aktive Verbindung besteht (LAZY CONNECT).

        Bei injizierter Connection: nur Ping-Check.
        Ohne injizierte Connection: Verbindung wird bei Bedarf aufgebaut.

        Raises:
            OrbisConnectionError: Wenn Verbindung nicht herstellbar.
        """
        # Lazy Connect: Verbindung erst hier aufbauen wenn nötig
        if self.connection is None:
            if self._injected_connection:
                raise OrbisConnectionError("Injizierte Connection ist None")
            self._connect()
            return

        # Ping-Check für bestehende Verbindung
        try:
            self.connection.ping()
        except Exception as err:
            if self._injected_connection:
                # Bei injizierter Connection keine automatische Recovery,
                # Fehler explizit weitergeben
                raise OrbisConnectionError("Injizierte Connection nicht erreichbar") from err
            logger.warning("Orbis-Verbindung verloren, versuche Reconnect...")
            self._connect()

    def lookup_batch_by_websites(
        self,
        domains: list[str],
        batch_size: Optional[int] = None,
    ) -> dict[str, dict[str, Any]]:
        """Sucht Orbis-Einträge für eine Liste von Domains.

        Führt Batch-Queries mit IN-Clause gegen die CONTACT_INFO-Tabelle aus.
        Domains werden case-insensitiv gematcht.

        CONSECUTIVE ERROR HANDLING: Nach MAX_CONSECUTIVE_ERRORS aufeinander-
        folgenden Batch-Fehlern wird der Lauf abgebrochen.

        Args:
            domains: Liste von normalisierten Domains (Kleinbuchstaben, ohne www.)
            batch_size: Anzahl Domains pro Query. Default aus ORBIS_CONFIG.

        Returns:
            Dictionary mit Domain als Key und Orbis-Daten als Value:
            {
                "acme.com": {
                    "bvd_id": "XX000008",
                    "name": "ACME Corporation GmbH",
                    "country_iso": "DE",
                    "city": "Berlin",
                    "postcode": "10115",
                    "website": "www.acme.com",
                    "latin_name": None,  # nur mit ORBIS_LATIN_NAME_COLUMN befüllt
                },
                ...
            }

        Raises:
            OrbisQueryError: Bei Query-Fehlern oder nach zu vielen
                             aufeinanderfolgenden Fehlern.
            OrbisConnectionError: Bei Verbindungsfehlern.
        """
        if not domains:
            return {}

        self._ensure_connection()

        # Reduzierte Batch-Größe wegen URL-Varianten (12 pro Domain) - der
        # konfigurierte generische batch_size darf das Oracle-IN-Clause-Limit
        # nicht überschreiten (siehe MAX_DOMAINS_PER_BATCH-Kommentar oben).
        batch_size = min(batch_size or self.config.get("batch_size", 80), MAX_DOMAINS_PER_BATCH)
        schema = self.config.get("schema", "ORBIS_23J")
        results: dict[str, dict[str, Any]] = {}

        # Dedupliziere und normalisiere Domains
        unique_domains = list({d.lower().strip() for d in domains if d})

        logger.info(
            "Starte Orbis-Lookup für %d Domains (Batch-Größe: %d)",
            len(unique_domains),
            batch_size,
        )

        # Reset consecutive error counter at start of lookup
        self.consecutive_errors = 0

        # Verarbeite in Batches
        total_batches = (len(unique_domains) + batch_size - 1) // batch_size
        for i in range(0, len(unique_domains), batch_size):
            batch = unique_domains[i : i + batch_size]
            batch_num = (i // batch_size) + 1

            try:
                batch_results = self._execute_website_batch_query(batch, schema)
                results.update(batch_results)
                # Reset error counter on success
                self.consecutive_errors = 0

                logger.debug(
                    "Batch %d/%d verarbeitet: %d Treffer",
                    batch_num,
                    total_batches,
                    len(batch_results),
                )

            except OrbisQueryError as e:
                self.consecutive_errors += 1
                logger.error(
                    "Batch %d/%d fehlgeschlagen (Fehler %d/%d): %s",
                    batch_num,
                    total_batches,
                    self.consecutive_errors,
                    MAX_CONSECUTIVE_ERRORS,
                    e,
                )

                if self.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    raise OrbisQueryError(
                        f"Abbruch nach {MAX_CONSECUTIVE_ERRORS} aufeinanderfolgenden "
                        f"Batch-Fehlern. Letzter Fehler: {e}"
                    ) from e

        logger.info(
            "Orbis-Lookup abgeschlossen: %d/%d Domains gefunden",
            len(results),
            len(unique_domains),
        )

        return results

    def _execute_website_batch_query(
        self,
        domains: list[str],
        schema: str,
    ) -> dict[str, dict[str, Any]]:
        """Führt eine einzelne Batch-Query für Domains aus (dedupliziert).

        Dünner Wrapper um `_execute_all_candidates_query()`: liefert nur
        den ERSTEN Treffer pro Domain (MEHRFACH-TREFFER-STRATEGIE, Query ist
        nach BVD_ID sortiert für Reproduzierbarkeit). Ein Warning wird
        geloggt, wenn eine Domain mehrere Treffer hatte. Die eigentliche
        URL-Varianten-/Fallback-Logik lebt komplett in
        `_execute_all_candidates_query()` - vorher pflegte diese Methode
        eine eigene, fast identische Kopie davon.

        Args:
            domains: Liste von Domains für diese Batch.
            schema: Oracle Schema Name (z.B. "ORBIS_23J").

        Returns:
            Dictionary {domain: orbis_data} für gefundene Einträge.

        Raises:
            OrbisQueryError: Bei Query-Fehlern.
        """
        if not domains or self.connection is None:
            return {}

        all_candidates = self._execute_all_candidates_query(domains, schema)

        results: dict[str, dict[str, Any]] = {}
        multi_hit_domains: dict[str, int] = {}
        for matched_domain, candidates in all_candidates.items():
            if not candidates:
                continue
            results[matched_domain] = candidates[0]
            if len(candidates) > 1:
                multi_hit_domains[matched_domain] = len(candidates)

        if multi_hit_domains:
            logger.warning(
                "Mehrfach-Treffer für %d Domains (erster BVD_ID verwendet): %s",
                len(multi_hit_domains),
                dict(list(multi_hit_domains.items())[:5]),  # Show max 5
            )

        return results

    # Komplexität bewusst akzeptiert (noqa statt Aufsplitten): Batching,
    # Root-Domain-Fallback-Expansion, Fehlerbudget-Retry und die getrennte
    # Zusammenführung von Direkt- vs. Fallback-Treffern gehören inhaltlich
    # zusammen - ein Aufteilen würde v.a. den Kontrollfluss über mehrere
    # Funktionen verteilen, ohne die Logik selbst zu vereinfachen.
    def lookup_batch_by_websites_all_candidates(  # noqa: C901
        self,
        domains: list[str],
        include_root_domain_fallback: bool = True,
        max_candidates_per_domain: int = MAX_CANDIDATES_PER_DOMAIN,
        root_domain_blacklist: Optional[set[str]] = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Sucht ALLE Orbis-Kandidaten für eine Liste von Domains (kein Dedup).

        Additive Ergänzung zu `lookup_batch_by_websites()`: jene Methode
        dedupliziert intern auf einen Treffer pro Domain ("erster BVDID
        alphabetisch gewinnt") BEVOR die Scoring-Schicht (orbis_matching_logic,
        hq_detection) überhaupt mehrere Kandidaten sehen kann - dadurch kann
        die Namens-Plausibilitätsprüfung und HQ-Kaskade nie greifen. Diese
        Methode gibt bewusst alle Kandidaten zurück, damit die Scoring-Schicht
        selbst entscheidet. Siehe
        Orbis-Notizen (intern): validation_batch2_baseline.md.

        Root-Domain-Fallback (siehe `extract_root_domain()`): wenn eine Domain
        eine Subdomain ist (z.B. `opensource.sap.com`), wird zusätzlich die
        Root-Domain (`sap.com`) mitgesucht - Ergebnisse werden unter dem
        ORIGINAL angefragten Domain-Key zurückgegeben, mit einem Feld
        `matched_via_root_domain` markiert.

        WICHTIG (Fund aus Charge 11): die berechnete Root-Domain wurde bisher
        NICHT gegen die Blog-Blacklist geprüft - nur die ursprünglich
        angefragte Domain (das prüft der Caller vor dem Aufruf dieser
        Methode). Für `engineeringblog.yelp.com` ergab das eine Root-Domain
        `yelp.com`, die selbst nicht blacklisted war, und lieferte 1105
        komplett unbeteiligte Kandidaten (viele kleine Firmen haben
        offenbar ihre Yelp-Profilseite statt ihrer echten Website in Orbis
        hinterlegt). `root_domain_blacklist` schließt genau diese Lücke.

        Args:
            domains: Liste von normalisierten Domains (Kleinbuchstaben, ohne www.).
            include_root_domain_fallback: Ob bei Subdomains zusätzlich die
                Root-Domain durchsucht werden soll.
            max_candidates_per_domain: Obergrenze pro Domain (Schutz gegen
                Speicher-Explosion bei extrem domain-mehrfachgenutzten Fällen).
            root_domain_blacklist: Optionales Blacklist-Set (siehe
                `load_blog_blacklist()`), gegen das JEDE berechnete
                Root-Domain geprüft wird, bevor sie zusätzlich durchsucht
                wird. Blacklisted Root-Domains werden übersprungen.

        Returns:
            Dictionary mit der ANGEFRAGTEN Domain als Key und einer Liste
            aller gefundenen Orbis-Kandidaten als Value (kann leer sein).
            Jeder Kandidat: {bvd_id, name, country_iso, city, postcode,
            website, latin_name, matched_via_root_domain}. `latin_name` ist der
            Name aus der zweiten, lateinischen Namensspalte, falls
            `ORBIS_LATIN_NAME_COLUMN` gesetzt ist (siehe `latin_name_column()`),
            sonst None.

        Raises:
            OrbisQueryError: Bei Query-Fehlern oder nach zu vielen
                             aufeinanderfolgenden Fehlern.
            OrbisConnectionError: Bei Verbindungsfehlern.
        """
        if not domains:
            return {}

        self._ensure_connection()

        # Gleiche Begrenzung wie in lookup_batch_by_websites() - 12
        # URL-Varianten pro Domain, Oracle-IN-Clause-Limit darf nicht
        # überschritten werden (siehe MAX_DOMAINS_PER_BATCH-Kommentar oben).
        batch_size = min(self.config.get("batch_size", 80), MAX_DOMAINS_PER_BATCH)
        schema = self.config.get("schema", "ORBIS_23J")
        results: dict[str, list[dict[str, Any]]] = {d: [] for d in domains}

        unique_domains = list({d.lower().strip() for d in domains if d})

        # Root-Domain-Fallback-Kandidaten sammeln (Subdomain -> Root-Domain-Suchbegriff)
        root_domain_for: dict[str, str] = {}
        search_domains = set(unique_domains)
        if include_root_domain_fallback:
            for d in unique_domains:
                root = extract_root_domain(d)
                if root and not is_domain_blacklisted(root, root_domain_blacklist or set()):
                    root_domain_for[root] = d
                    search_domains.add(root)

        logger.info(
            "Starte Orbis-Lookup (alle Kandidaten) für %d Domains "
            "(inkl. %d Root-Domain-Fallbacks)",
            len(unique_domains),
            len(root_domain_for),
        )

        self.consecutive_errors = 0
        search_list = list(search_domains)
        total_batches = (len(search_list) + batch_size - 1) // batch_size
        for i in range(0, len(search_list), batch_size):
            batch = search_list[i : i + batch_size]
            batch_num = (i // batch_size) + 1
            logger.info("Domain-Batch %d/%d (%d Domains)...", batch_num, total_batches, len(batch))
            try:
                batch_results = self._execute_all_candidates_query(batch, schema)
            except OrbisQueryError as e:
                self.consecutive_errors += 1
                logger.error(
                    "Batch fehlgeschlagen (Fehler %d/%d): %s",
                    self.consecutive_errors,
                    MAX_CONSECUTIVE_ERRORS,
                    e,
                )
                if self.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    raise OrbisQueryError(
                        f"Abbruch nach {MAX_CONSECUTIVE_ERRORS} aufeinanderfolgenden "
                        f"Batch-Fehlern. Letzter Fehler: {e}"
                    ) from e
                continue
            self.consecutive_errors = 0

            for matched_domain, candidates in batch_results.items():
                candidates = candidates[:max_candidates_per_domain]

                if matched_domain in results:
                    # Direkter Treffer auf eine angefragte Domain
                    for c in candidates:
                        c["matched_via_root_domain"] = False
                    results[matched_domain].extend(candidates)

                original = root_domain_for.get(matched_domain)
                if original:
                    # Treffer über den Root-Domain-Fallback
                    for c in candidates:
                        results[original].append({**c, "matched_via_root_domain": True})

        total_candidates = sum(len(v) for v in results.values())
        logger.info(
            "Orbis-Lookup (alle Kandidaten) abgeschlossen: %d Kandidaten für %d Domains",
            total_candidates,
            len(unique_domains),
        )
        return results

    # Komplexität bewusst akzeptiert (noqa statt Aufsplitten): URL-Varianten-
    # Aufbau, Bind-Variablen, Row-Parsing mit defensivem Spaltenformat-Check
    # und die Domain-Normalisierungs-Fallback-Kette (falls Orbis' WEBSITE-Feld
    # nicht exakt einer Such-Variante entspricht) hängen zu eng zusammen, um
    # sie ohne zusätzliche Kopplung über mehrere Funktionen zu verteilen.
    def _execute_all_candidates_query(  # noqa: C901
        self,
        domains: list[str],
        schema: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """Wie `_execute_website_batch_query()`, aber ohne Dedup pro Domain."""
        if not domains or self.connection is None:
            return {}

        all_variants = []
        domain_to_variants = {}
        for domain in domains:
            variants = [
                domain.lower(),
                f"www.{domain}".lower(),
                f"http://{domain}".lower(),
                f"http://www.{domain}".lower(),
                f"https://{domain}".lower(),
                f"https://www.{domain}".lower(),
                f"{domain}/".lower(),
                f"www.{domain}/".lower(),
                f"http://{domain}/".lower(),
                f"http://www.{domain}/".lower(),
                f"https://{domain}/".lower(),
                f"https://www.{domain}/".lower(),
            ]
            for v in variants:
                all_variants.append(v)
                domain_to_variants[v] = domain

        bind_names = [f":v{i}" for i in range(len(all_variants))]
        bind_vars = {f"v{i}": variant for i, variant in enumerate(all_variants)}

        latin_column = latin_name_column()
        extra_columns = f", {latin_column}" if latin_column else ""
        expected_columns = _BASE_CANDIDATE_COLUMNS + bool(latin_column)
        query = f"""
            SELECT BVDID, NAME_NATIVE, WEBSITE, CTRYISO, CITY_NATIVE, POSTCODE{extra_columns}
            FROM {schema}.CONTACT_INFO
            WHERE LOWER(WEBSITE) IN ({', '.join(bind_names)})
            AND BVDID IS NOT NULL
            ORDER BY CASE WHEN BVDID NOT LIKE '%-B%' THEN 0 ELSE 1 END, BVDID
        """

        results: dict[str, list[dict[str, Any]]] = {}
        try:
            cursor = self.connection.cursor()
            try:
                cursor.execute(query, bind_vars)
                for row in cursor:
                    if not row or len(row) != expected_columns:
                        logger.warning(
                            "Unerwartetes Spaltenformat in Orbis-Response: %d Spalten statt %d",
                            len(row) if row else 0,
                            expected_columns,
                        )
                        continue
                    bvd_id, name, website, country_iso, city, postcode = row[:6]
                    latin_name = row[6] if latin_column else None

                    if not website:
                        continue

                    website_lower = website.lower()
                    matched_domain = domain_to_variants.get(website_lower)

                    if not matched_domain:
                        website_clean = website_lower
                        for prefix in ["https://", "http://", "www."]:
                            if website_clean.startswith(prefix):
                                website_clean = website_clean[len(prefix) :]
                        website_clean = website_clean.rstrip("/")
                        matched_domain = domain_to_variants.get(website_clean)

                    if not matched_domain:
                        for domain in domains:
                            if domain in website_lower:
                                matched_domain = domain
                                break

                    if not matched_domain:
                        continue

                    results.setdefault(matched_domain, []).append(
                        {
                            "bvd_id": bvd_id,
                            "name": name,
                            "country_iso": country_iso,
                            "city": city,
                            "postcode": postcode,
                            "website": website,
                            "latin_name": latin_name,
                        }
                    )
            finally:
                cursor.close()
        except Exception as e:
            error_msg = f"Orbis-Query fehlgeschlagen: {e}"
            logger.error(error_msg)
            raise OrbisQueryError(error_msg) from e

        return results

    def lookup_batch_by_names(
        self,
        names: list[str],
        batch_size: Optional[int] = None,
        max_candidates_per_name: int = MAX_CANDIDATES_PER_DOMAIN,
    ) -> dict[str, list[dict[str, Any]]]:
        """Sucht Orbis-Kandidaten per exaktem Namensabgleich (NAME_NATIVE).

        Nutzt den bestehenden Function-Based-Index auf LOWER(NAME_NATIVE)
        (per EXPLAIN PLAN gegen Orbis bestätigt).
        Für den echten Name-Only-Pfad (Orgs ohne verwertbare Domain) - bisher
        gab es dafür keine Batch-Methode auf dem Client, nur Ad-hoc-SQL in
        einzelnen Skripten.

        Args:
            names: Liste von Namen, Groß-/Kleinschreibung beliebig (wird
                intern normalisiert - siehe Fund unten).
            batch_size: Anzahl Namen pro Query. Default aus ORBIS_CONFIG.
            max_candidates_per_name: Obergrenze pro Name.

        Returns:
            Dictionary mit dem angefragten Namen in Kleinbuchstaben (`.lower()
            .strip()`) als Key und einer Liste aller gefundenen Kandidaten als
            Value.

        Raises:
            OrbisQueryError: Bei Query-Fehlern.
            OrbisConnectionError: Bei Verbindungsfehlern.
        """
        if not names:
            return {}

        self._ensure_connection()

        batch_size = batch_size or self.config.get("batch_size", 80)
        schema = self.config.get("schema", "ORBIS_23J")

        # WICHTIG: Keys müssen normalisiert (lower/strip) sein, nicht die
        # rohen Eingabe-Strings - sonst schlägt der Ergebnis-Lookup unten
        # (key = name.lower()) für jeden nicht bereits kleingeschriebenen
        # Eingabe-Namen fehl und liefert stillschweigend 0 Kandidaten (Fund
        # aus Charge 10: ein großgeschriebener Name lieferte 0 statt mehrerer Treffer,
        # weil der alte Code hier die unveränderte Eingabe als Key nahm).
        unique_names = list({n.lower().strip() for n in names if n})
        results: dict[str, list[dict[str, Any]]] = {n: [] for n in unique_names}

        for i in range(0, len(unique_names), batch_size):
            batch = unique_names[i : i + batch_size]
            bind_names = [f":n{j}" for j in range(len(batch))]
            bind_vars = {f"n{j}": name for j, name in enumerate(batch)}

            query = f"""
                SELECT BVDID, NAME_NATIVE, WEBSITE, CTRYISO, CITY_NATIVE
                FROM {schema}.CONTACT_INFO
                WHERE LOWER(NAME_NATIVE) IN ({', '.join(bind_names)})
                AND BVDID IS NOT NULL
            """

            try:
                cursor = self.connection.cursor()
                try:
                    cursor.execute(query, bind_vars)
                    for row in cursor:
                        try:
                            bvd_id, name, website, country_iso, city = row
                        except ValueError:
                            continue
                        if not name:
                            continue
                        key = name.lower()
                        if key in results and len(results[key]) < max_candidates_per_name:
                            results[key].append(
                                {
                                    "bvd_id": bvd_id,
                                    "name": name,
                                    "country_iso": country_iso,
                                    "city": city,
                                    "website": website,
                                }
                            )
                finally:
                    cursor.close()
            except Exception as e:
                raise OrbisQueryError(f"Orbis-Namens-Query fehlgeschlagen: {e}") from e

        return results

    def lookup_hq_subsidiary_counts(
        self,
        bvd_ids: list[str],
        batch_size: Optional[int] = None,
    ) -> dict[str, int]:
        """Zähle pro BVDID, wie viele Töchter sie aktuell als Hauptsitz nennen.

        WICHTIG - Join-Richtung: Die ORBIS_23J.HQ-Tabelle hat pro Zeile ein
        `BVDID` (die Tochter/Niederlassung) und ein `HQ_BVDID` (ihr
        Hauptsitz). Der in `orbis_matching_strategy_v2.md` (Abschnitt 5.4)
        dokumentierte Join `h.BVDID = c.BVDID` sucht also nach Zeilen, bei
        denen der KANDIDAT SELBST eine Tochter ist - das liefert für echte
        Konzernmütter (die keinen eigenen übergeordneten Hauptsitz haben)
        praktisch nie einen Treffer (empirisch verifiziert: 0 von 13
        getesteten bekannten Konzernmutter-BVDIDs, u.a. IBM/Oracle/Airbus).
        Der richtige Join ist umgekehrt: `HQ_BVDID = candidate.bvd_id` -
        "wie viele andere Firmen nennen DIESEN Kandidaten als ihren
        Hauptsitz". Empirisch bestätigt an großen Konzernmüttern, die für
        sehr viele Töchter als HQ_BVDID hinterlegt sind.

        Filtert zusätzlich auf `HQ_CURRENT_INDICATOR = 'Current info'`
        (verwirft `'Archived info'`-Zeilen) - vermeidet dieselbe Falle wie
        beim historisch gescheiterten OLDEST_FOUNDING-Signal (siehe
        `hq_detection.py`-Modul-Docstring): veraltete statt aktuelle
        Information zu bevorzugen.

        Args:
            bvd_ids: Liste von BVDIDs (z.B. aus bereits gefundenen
                Match-Kandidaten), für die die Tochter-Anzahl ermittelt
                werden soll.
            batch_size: Anzahl BVDIDs pro Query. Default aus ORBIS_CONFIG.

        Returns:
            Dictionary BVDID -> Anzahl aktueller Töchter, die diese BVDID
            als Hauptsitz nennen. BVDIDs ohne Treffer sind mit 0 enthalten.

        Raises:
            OrbisQueryError: Bei Query-Fehlern.
            OrbisConnectionError: Bei Verbindungsfehlern.
        """
        if not bvd_ids:
            return {}

        self._ensure_connection()

        batch_size = batch_size or self.config.get("batch_size", 80)
        schema = self.config.get("schema", "ORBIS_23J")

        unique_ids = list({b for b in bvd_ids if b})
        counts: dict[str, int] = dict.fromkeys(unique_ids, 0)

        for i in range(0, len(unique_ids), batch_size):
            batch = unique_ids[i : i + batch_size]
            bind_names = [f":h{j}" for j in range(len(batch))]
            bind_vars = {f"h{j}": bvd_id for j, bvd_id in enumerate(batch)}

            query = f"""
                SELECT HQ_BVDID, COUNT(*)
                FROM {schema}.HQ
                WHERE HQ_BVDID IN ({', '.join(bind_names)})
                AND HQ_CURRENT_INDICATOR = 'Current info'
                GROUP BY HQ_BVDID
            """

            try:
                cursor = self.connection.cursor()
                try:
                    cursor.execute(query, bind_vars)
                    for hq_bvd_id, count in cursor:
                        if hq_bvd_id in counts:
                            counts[hq_bvd_id] = count
                finally:
                    cursor.close()
            except Exception as e:
                raise OrbisQueryError(f"Orbis-HQ-Query fehlgeschlagen: {e}") from e

        return counts

    def test_connection(self) -> bool:
        """Testet die Verbindung zur Orbis-Datenbank.

        Returns:
            True wenn die Verbindung funktioniert, sonst False.
        """
        try:
            self._ensure_connection()
            if self.connection:
                cursor = self.connection.cursor()
                try:
                    cursor.execute("SELECT 1 FROM DUAL")
                    result = cursor.fetchone()
                    return result is not None and result[0] == 1
                finally:
                    cursor.close()
        except (OrbisConnectionError, OrbisQueryError) as e:
            logger.error("Verbindungstest fehlgeschlagen: %s", e)
            return False
        except Exception as e:
            logger.error("Verbindungstest fehlgeschlagen (unerwarteter Fehler): %s", e)
            return False

        return False

    def get_table_info(self, table_name: str = "CONTACT_INFO") -> dict[str, Any]:
        """Gibt Informationen über eine Orbis-Tabelle zurück.

        Args:
            table_name: Name der Tabelle (default: CONTACT_INFO).

        Returns:
            Dictionary mit Tabelleninformationen (Spalten, Row-Count Estimate).

        Raises:
            OrbisQueryError: Bei Query-Fehlern.
        """
        self._ensure_connection()

        if self.connection is None:
            raise OrbisConnectionError("Keine aktive Verbindung")

        schema = self.config.get("schema", "ORBIS_23J")
        info: dict[str, Any] = {"table": f"{schema}.{table_name}", "columns": []}

        try:
            cursor = self.connection.cursor()
            try:
                # Hole Spalteninformationen
                cursor.execute(
                    """
                    SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH
                    FROM ALL_TAB_COLUMNS
                    WHERE OWNER = :schema AND TABLE_NAME = :table_name
                    ORDER BY COLUMN_ID
                    """,
                    {"schema": schema, "table_name": table_name},
                )

                for row in cursor:
                    info["columns"].append({"name": row[0], "type": row[1], "length": row[2]})

                # Hole geschätzte Row-Count (schneller als COUNT(*))
                cursor.execute(
                    """
                    SELECT NUM_ROWS
                    FROM ALL_TABLES
                    WHERE OWNER = :schema AND TABLE_NAME = :table_name
                    """,
                    {"schema": schema, "table_name": table_name},
                )
                result = cursor.fetchone()
                info["estimated_rows"] = result[0] if result else None
            finally:
                cursor.close()

        except Exception as e:
            error_msg = f"Fehler beim Abrufen der Tabelleninfo: {e}"
            logger.error(error_msg)
            raise OrbisQueryError(error_msg) from e

        return info

    def close(self) -> None:
        """Schließt die Oracle-Verbindung."""
        if self.connection:
            try:
                self.connection.close()
                logger.info("Oracle Orbis Verbindung geschlossen")
            except Exception as e:
                logger.warning("Fehler beim Schließen der Verbindung: %s", e)
            finally:
                self.connection = None

    def __enter__(self) -> "OrbisClient":
        """Context Manager Entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context Manager Exit."""
        self.close()
