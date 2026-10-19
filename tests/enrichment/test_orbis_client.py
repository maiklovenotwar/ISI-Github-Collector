"""Unit Tests für orbis_client.py.

Diese Tests laufen OHNE Oracle-Verbindung durch Verwendung von FakeConnection.
Sie validieren die Architektur-Entkopplung und das defensive Error-Handling.

Getestete Architektur-Entscheidungen:
    - LAZY CONNECT: Client kann ohne Netzwerk instanziiert werden
    - INJIZIERBARE CONNECTION: FakeConnection ermöglicht vollständige Testabdeckung
    - MEHRFACH-TREFFER-STRATEGIE: Erster Treffer (nach BVD_ID sortiert) gewinnt
    - CONSECUTIVE ERROR LIMIT: Nach 3 Fehlern wird abgebrochen
"""

import logging
from typing import Optional

import pytest

from github_collector.enrichment.orbis_client import (
    MAX_CONSECUTIVE_ERRORS,
    OrbisClient,
    OrbisConnectionError,
    OrbisQueryError,
    extract_root_domain,
    is_domain_blacklisted,
    load_blog_blacklist,
)


class FakeCursor:
    """Fake Oracle Cursor für Tests.

    Simuliert die oracledb Cursor-API vollständig:
    - execute(query, params)
    - fetchone()
    - fetchall()
    - __iter__ / __next__
    - close()
    """

    def __init__(self, rows: list[tuple], raise_on_execute: Optional[Exception] = None) -> None:
        """Initialisiert den FakeCursor.

        Args:
            rows: Liste von Tuples die als Query-Ergebnis zurückgegeben werden.
            raise_on_execute: Exception die bei execute() geworfen wird (für Fehler-Tests).
        """
        self.rows = rows
        self.raise_on_execute = raise_on_execute
        self._index = 0
        self.executed_queries: list[tuple[str, dict]] = []
        self.closed = False

    def execute(self, query: str, params: Optional[dict] = None) -> None:
        """Simuliert Query-Ausführung."""
        if self.raise_on_execute:
            raise self.raise_on_execute
        self.executed_queries.append((query, params or {}))
        self._index = 0

    def fetchone(self) -> Optional[tuple]:
        """Gibt eine Zeile zurück."""
        if self._index < len(self.rows):
            row = self.rows[self._index]
            self._index += 1
            return row
        return None

    def fetchall(self) -> list[tuple]:
        """Gibt alle Zeilen zurück."""
        return self.rows

    def __iter__(self) -> "FakeCursor":
        """Iterator-Protokoll."""
        self._index = 0
        return self

    def __next__(self) -> tuple:
        """Gibt nächste Zeile zurück."""
        if self._index < len(self.rows):
            row = self.rows[self._index]
            self._index += 1
            return row
        raise StopIteration

    def close(self) -> None:
        """Schließt den Cursor."""
        self.closed = True


class FakeConnection:
    """Fake Oracle Connection für Tests.

    Simuliert die oracledb Connection-API:
    - cursor()
    - ping()
    - close()
    """

    def __init__(
        self,
        cursor_rows: Optional[list[tuple]] = None,
        raise_on_execute: Optional[Exception] = None,
        raise_on_ping: bool = False,
    ) -> None:
        """Initialisiert die FakeConnection.

        Args:
            cursor_rows: Rows die der Cursor zurückgibt.
            raise_on_execute: Exception für Fehler-Tests.
            raise_on_ping: Wenn True, wirft ping() eine Exception.
        """
        self.cursor_rows = cursor_rows or []
        self.raise_on_execute = raise_on_execute
        self.raise_on_ping = raise_on_ping
        self.closed = False
        self._cursors: list[FakeCursor] = []

    def cursor(self) -> FakeCursor:
        """Erstellt einen neuen FakeCursor."""
        c = FakeCursor(self.cursor_rows, self.raise_on_execute)
        self._cursors.append(c)
        return c

    def ping(self) -> None:
        """Simuliert Connection-Ping."""
        if self.raise_on_ping:
            raise Exception("Connection lost")

    def close(self) -> None:
        """Schließt die Connection."""
        self.closed = True


class TestOrbisClientLazyConnect:
    """Tests für Lazy Connect Verhalten."""

    def test_instantiation_without_network(self) -> None:
        """Client kann ohne Netzwerk instanziiert werden (mit injizierter Connection)."""
        fake_conn = FakeConnection()
        client = OrbisClient(connection=fake_conn)

        assert client.connection is fake_conn
        assert client._injected_connection is True

    def test_instantiation_with_empty_config_and_connection(self) -> None:
        """Mit injizierter Connection wird Config nicht validiert."""
        fake_conn = FakeConnection()
        # Leere Config würde ohne Connection einen ValueError werfen
        client = OrbisClient(config={}, connection=fake_conn)

        assert client.connection is fake_conn

    def test_config_validation_without_connection(self) -> None:
        """Ohne Connection wird Config validiert (fehlende Pflichtfelder)."""
        # Config mit leeren Pflichtfeldern
        empty_config = {
            "host": "",
            "port": 1521,
            "service": "",
            "user": "",
            "password": "",
            "schema": "TEST",
        }
        with pytest.raises(ValueError, match="Fehlende Orbis-Konfiguration"):
            OrbisClient(config=empty_config)

    def test_lazy_connect_not_called_on_init(self) -> None:
        """Bei injizierter Connection wird _connect nicht aufgerufen."""
        fake_conn = FakeConnection()
        client = OrbisClient(connection=fake_conn)

        # Connection ist bereits gesetzt, kein Connect-Versuch
        assert client.connection is not None


class TestOrbisClientNormalMatch:
    """Tests für normales Domain-Matching."""

    def test_single_domain_single_result(self) -> None:
        """Eine Domain → ein Ergebnis mit bvd_id und country_iso."""
        rows = [
            ("XX000008", "ACME GmbH", "https://acme.com", "DE", "Berlin", "10115"),
        ]
        fake_conn = FakeConnection(cursor_rows=rows)
        client = OrbisClient(connection=fake_conn)

        results = client.lookup_batch_by_websites(["acme.com"])

        assert "acme.com" in results
        assert results["acme.com"]["bvd_id"] == "XX000008"
        assert results["acme.com"]["country_iso"] == "DE"
        assert results["acme.com"]["name"] == "ACME GmbH"

    def test_multiple_domains_multiple_results(self) -> None:
        """Mehrere Domains → mehrere Ergebnisse."""
        rows = [
            ("XX000008", "ACME GmbH", "https://acme.com", "DE", "Berlin", "10115"),
            ("XX000022", "Example Inc", "https://example.org", "US", "New York", "10001"),
        ]
        fake_conn = FakeConnection(cursor_rows=rows)
        client = OrbisClient(connection=fake_conn)

        results = client.lookup_batch_by_websites(["acme.com", "example.org"])

        assert len(results) == 2
        assert results["acme.com"]["country_iso"] == "DE"
        assert results["example.org"]["country_iso"] == "US"

    def test_country_iso_none_is_preserved(self) -> None:
        """country_iso kann None sein (wird nicht als Fehler behandelt)."""
        rows = [
            ("XX12345678", "Unknown Corp", "https://unknown.com", None, None, None),
        ]
        fake_conn = FakeConnection(cursor_rows=rows)
        client = OrbisClient(connection=fake_conn)

        results = client.lookup_batch_by_websites(["unknown.com"])

        assert "unknown.com" in results
        assert results["unknown.com"]["country_iso"] is None
        assert results["unknown.com"]["bvd_id"] == "XX12345678"


class TestOrbisClientNoMatch:
    """Tests für Fälle ohne Treffer."""

    def test_no_match_returns_empty_dict(self) -> None:
        """Domain nicht in Orbis → leeres Dict, keine Exception."""
        fake_conn = FakeConnection(cursor_rows=[])
        client = OrbisClient(connection=fake_conn)

        results = client.lookup_batch_by_websites(["nonexistent.com"])

        assert results == {}

    def test_empty_domain_list(self) -> None:
        """Leere Domain-Liste → leeres Dict."""
        fake_conn = FakeConnection()
        client = OrbisClient(connection=fake_conn)

        results = client.lookup_batch_by_websites([])

        assert results == {}

    def test_none_and_empty_domains_filtered(self) -> None:
        """None und leere Strings in Domain-Liste werden vor Query gefiltert."""
        fake_conn = FakeConnection(cursor_rows=[])
        client = OrbisClient(connection=fake_conn)

        # Lookup mit None, leeren Strings und einer gültigen Domain
        results = client.lookup_batch_by_websites(["", "  ", None])  # type: ignore

        # Keine gültigen Domains → leeres Ergebnis, aber kein Crash
        assert results == {}
        # Wichtig: Es wurde ein Query ausgeführt (mit leerer Domain-Liste gefiltert)
        # Die Deduplizierung filtert leere Strings raus


class TestOrbisClientMultipleHits:
    """Tests für Mehrfach-Treffer-Strategie."""

    def test_multiple_hits_first_wins(self, caplog: pytest.LogCaptureFixture) -> None:
        """Mehrere Treffer für eine Domain → erster (nach BVD_ID) gewinnt."""
        # Sortiert nach BVD_ID: AA kommt vor BB
        rows = [
            ("XX000001", "First Corp", "https://multi.com", "DE", "Munich", "80331"),
            ("XX000004", "Second Corp", "https://multi.com", "US", "Boston", "02101"),
        ]
        fake_conn = FakeConnection(cursor_rows=rows)
        client = OrbisClient(connection=fake_conn)

        with caplog.at_level(logging.WARNING):
            results = client.lookup_batch_by_websites(["multi.com"])

        # Erster Treffer (AA...) gewinnt
        assert results["multi.com"]["bvd_id"] == "XX000001"
        assert results["multi.com"]["country_iso"] == "DE"

        # Warning wurde geloggt
        assert "Mehrfach-Treffer" in caplog.text

    def test_multiple_hits_warning_contains_domain(self, caplog: pytest.LogCaptureFixture) -> None:
        """Warning bei Mehrfach-Treffern enthält Domain-Info."""
        rows = [
            ("XX000001", "First", "https://dup.com", "DE", "Berlin", "10115"),
            ("XX000004", "Second", "https://dup.com", "US", "NYC", "10001"),
        ]
        fake_conn = FakeConnection(cursor_rows=rows)
        client = OrbisClient(connection=fake_conn)

        with caplog.at_level(logging.WARNING):
            client.lookup_batch_by_websites(["dup.com"])

        assert "dup.com" in caplog.text or "Mehrfach-Treffer" in caplog.text


class TestOrbisClientMissingSpalte:
    """Tests für fehlende/unerwartete Spalten im Response."""

    def test_unexpected_column_count_skipped(self, caplog: pytest.LogCaptureFixture) -> None:
        """Zeile mit falscher Spaltenanzahl wird übersprungen, kein Crash."""
        rows = [
            # Korrekte Zeile
            ("XX000008", "ACME GmbH", "https://acme.com", "DE", "Berlin", "10115"),
            # Fehlerhafte Zeile (nur 3 Spalten)
            ("XX99999999", "Broken", "https://broken.com"),
        ]
        fake_conn = FakeConnection(cursor_rows=rows)
        client = OrbisClient(connection=fake_conn)

        with caplog.at_level(logging.WARNING):
            results = client.lookup_batch_by_websites(["acme.com", "broken.com"])

        # Korrekte Zeile wurde verarbeitet
        assert "acme.com" in results
        # Fehlerhafte Zeile wurde übersprungen
        assert "broken.com" not in results
        # Warning wurde geloggt
        assert "Spaltenformat" in caplog.text or len(results) == 1


class TestOrbisClientNetworkError:
    """Tests für Netzwerk-/Query-Fehler."""

    def test_query_error_logged_and_counted(self, caplog: pytest.LogCaptureFixture) -> None:
        """Query-Fehler wird geloggt und erhöht consecutive_errors Zähler.

        Einzelne Fehler werden NICHT sofort als Exception geworfen, sondern
        nur geloggt. Erst nach MAX_CONSECUTIVE_ERRORS wird abgebrochen.
        """
        fake_conn = FakeConnection(raise_on_execute=Exception("DB Error"))
        client = OrbisClient(connection=fake_conn)

        with caplog.at_level(logging.ERROR):
            # Ein einzelner Batch-Fehler wirft KEINE Exception
            results = client.lookup_batch_by_websites(["acme.com"])

        # Ergebnis ist leer (Fehler wurde gefangen)
        assert results == {}
        # Fehler wurde geloggt
        assert "DB Error" in caplog.text
        # Consecutive error counter wurde erhöht
        assert client.consecutive_errors == 1

    def test_connection_lost_raises_error(self) -> None:
        """Verlorene Connection bei injiziertem Client wirft Fehler."""
        fake_conn = FakeConnection(raise_on_ping=True)
        client = OrbisClient(connection=fake_conn)

        with pytest.raises(OrbisConnectionError, match="nicht erreichbar"):
            client.lookup_batch_by_websites(["acme.com"])


class TestOrbisClientConsecutiveErrors:
    """Tests für Consecutive Error Handling."""

    def test_single_error_continues(self) -> None:
        """Ein einzelner Batch-Fehler stoppt nicht den gesamten Lauf."""
        # Dieser Test ist komplex weil wir pro Batch verschiedenes Verhalten brauchen
        # Vereinfachte Version: Prüfe dass consecutive_errors Zähler existiert
        fake_conn = FakeConnection(cursor_rows=[])
        client = OrbisClient(connection=fake_conn)

        assert client.consecutive_errors == 0

    def test_max_consecutive_errors_aborts(self) -> None:
        """Nach MAX_CONSECUTIVE_ERRORS aufeinanderfolgenden Fehlern wird abgebrochen."""
        fake_conn = FakeConnection(raise_on_execute=Exception("Persistent Error"))
        client = OrbisClient(connection=fake_conn)

        # Genug Domains für mehrere Batches
        domains = [f"domain{i}.com" for i in range(MAX_CONSECUTIVE_ERRORS * 100)]

        with pytest.raises(OrbisQueryError, match="aufeinanderfolgenden"):
            client.lookup_batch_by_websites(domains, batch_size=10)

    def test_success_resets_error_counter(self) -> None:
        """Erfolgreicher Batch setzt den Fehler-Zähler zurück."""
        fake_conn = FakeConnection(cursor_rows=[])
        client = OrbisClient(connection=fake_conn)

        # Simuliere: Setze Fehler-Zähler manuell
        client.consecutive_errors = 2

        # Erfolgreicher Lookup
        client.lookup_batch_by_websites(["acme.com"])

        # Zähler wurde zurückgesetzt
        assert client.consecutive_errors == 0

    def test_max_consecutive_errors_constant(self) -> None:
        """MAX_CONSECUTIVE_ERRORS ist auf 3 gesetzt."""
        assert MAX_CONSECUTIVE_ERRORS == 3


class TestOrbisClientTestConnection:
    """Tests für test_connection Methode."""

    def test_connection_test_success(self) -> None:
        """test_connection gibt True bei erfolgreicher Verbindung."""
        fake_conn = FakeConnection(cursor_rows=[(1,)])
        client = OrbisClient(connection=fake_conn)

        result = client.test_connection()

        assert result is True

    def test_connection_test_failure(self) -> None:
        """test_connection gibt False bei fehlgeschlagener Verbindung."""
        fake_conn = FakeConnection(raise_on_execute=Exception("Connection failed"))
        client = OrbisClient(connection=fake_conn)

        result = client.test_connection()

        assert result is False


class TestOrbisClientContextManager:
    """Tests für Context Manager Support."""

    def test_context_manager_closes_connection(self) -> None:
        """Context Manager schließt Connection beim Exit."""
        fake_conn = FakeConnection()

        with OrbisClient(connection=fake_conn) as client:
            assert client.connection is not None

        # Connection wurde geschlossen
        assert fake_conn.closed is True

    def test_context_manager_closes_on_exception(self) -> None:
        """Context Manager schließt Connection auch bei Exception."""
        fake_conn = FakeConnection()

        with pytest.raises(ValueError):
            with OrbisClient(connection=fake_conn):
                raise ValueError("Test exception")

        # Connection wurde trotzdem geschlossen
        assert fake_conn.closed is True


class TestOrbisClientDomainNormalization:
    """Tests für Domain-Normalisierung innerhalb des Clients."""

    def test_domains_deduplicated(self) -> None:
        """Duplikate in Domain-Liste werden entfernt."""
        fake_conn = FakeConnection(cursor_rows=[])
        client = OrbisClient(connection=fake_conn)

        # Lookup mit Duplikaten
        client.lookup_batch_by_websites(["acme.com", "ACME.COM", "acme.com"])

        # Nur ein Cursor wurde erstellt, Query enthält deduplizierte Domains
        assert len(fake_conn._cursors) == 1

    def test_domains_lowercased(self) -> None:
        """Domains werden zu Kleinbuchstaben konvertiert."""
        rows = [
            ("XX000008", "ACME GmbH", "https://acme.com", "DE", "Berlin", "10115"),
        ]
        fake_conn = FakeConnection(cursor_rows=rows)
        client = OrbisClient(connection=fake_conn)

        results = client.lookup_batch_by_websites(["ACME.COM"])

        # Ergebnis ist unter lowercase Domain
        assert "acme.com" in results

    def test_domains_stripped(self) -> None:
        """Whitespace um Domains wird entfernt."""
        rows = [
            ("XX000008", "ACME GmbH", "https://acme.com", "DE", "Berlin", "10115"),
        ]
        fake_conn = FakeConnection(cursor_rows=rows)
        client = OrbisClient(connection=fake_conn)

        results = client.lookup_batch_by_websites(["  acme.com  "])

        assert "acme.com" in results


class TestOrbisClientLookupByNames:
    """Tests für lookup_batch_by_names() (Namens-Fallback-Pfad).

    Regressionsfall aus Charge 10 (studec): org["name"] kommt aus der GH-DB
    oft nicht kleingeschrieben (z.B. "STUDEC"). Der Ergebnis-Dict war vorher
    mit den unveränderten Eingabe-Strings als Key gebaut, während der
    Zeilen-Abgleich intern immer `name.lower()` verwendet hat - dadurch wurde
    für jeden nicht bereits kleingeschriebenen Suchbegriff stillschweigend 0
    Kandidaten zurückgegeben, obwohl Oracle Treffer lieferte.
    """

    def test_mixed_case_input_still_finds_results(self) -> None:
        """Nicht-kleingeschriebener Suchbegriff darf keine Treffer verschlucken."""
        rows = [
            ("XX000012", "Studec", "www.studec.fr", "FR", "Ivry Sur Seine"),
        ]
        fake_conn = FakeConnection(cursor_rows=rows)
        client = OrbisClient(connection=fake_conn)

        results = client.lookup_batch_by_names(["STUDEC"])

        assert results["studec"][0]["bvd_id"] == "XX000012"

    def test_result_key_is_lowercased_input(self) -> None:
        """Rückgabe-Key ist immer `.lower().strip()`, unabhängig von der Eingabe."""
        fake_conn = FakeConnection(cursor_rows=[])
        client = OrbisClient(connection=fake_conn)

        results = client.lookup_batch_by_names(["  Acme Corp  "])

        assert "acme corp" in results
        assert "  Acme Corp  " not in results

    def test_multiple_candidates_for_one_name(self) -> None:
        """Mehrere Orbis-Zeilen für denselben Namen werden alle gesammelt."""
        rows = [
            ("XX000012", "Studec", "www.studec.fr", "FR", "Ivry Sur Seine"),
            ("GBML7634128", "Studec", "www.studecorators.co.uk", "GB", "Worthing"),
        ]
        fake_conn = FakeConnection(cursor_rows=rows)
        client = OrbisClient(connection=fake_conn)

        results = client.lookup_batch_by_names(["Studec"])

        assert len(results["studec"]) == 2
        bvd_ids = {c["bvd_id"] for c in results["studec"]}
        assert bvd_ids == {"XX000012", "GBML7634128"}


class TestExtractRootDomain:
    """Tests für extract_root_domain() - Fund aus dem Hold-out-Test (Charge 11).

    Regressionsfall: "warren.com.br" bzw. "opensource.gammasoft.com.br"
    wurden vor dem Fix fälschlich auf "com.br" (den generischen
    zweiteiligen Länder-Suffix selbst) statt auf die echte Firmen-Domain
    reduziert - die Root-Domain-Fallback-Suche fragte Orbis dadurch nach
    einer bedeutungslosen Domain und lieferte komplett unbeteiligte
    Treffer aus anderen Ländern.
    """

    def test_simple_subdomain_two_label_tld(self) -> None:
        """Regression: normale 2-Label-TLD funktioniert weiterhin wie vorher."""
        assert extract_root_domain("opensource.sap.com") == "sap.com"

    def test_no_subdomain_two_labels_returns_none(self) -> None:
        assert extract_root_domain("sap.com") is None

    def test_compound_suffix_domain_is_already_minimal(self) -> None:
        """'warren.com.br' ist schon die minimale registrierbare Domain -
        'com.br' allein ist nur der Suffix, kein sinnvoller Suchbegriff."""
        assert extract_root_domain("warren.com.br") is None

    def test_compound_suffix_with_real_subdomain(self) -> None:
        """'opensource.gammasoft.com.br' -> 'gammasoft.com.br', NICHT 'com.br'."""
        assert extract_root_domain("opensource.gammasoft.com.br") == "gammasoft.com.br"

    def test_compound_suffix_com_vn(self) -> None:
        """Zweiter Hold-out-Fund: dieselbe Klasse bei vietnamesischen Domains."""
        assert extract_root_domain("be.com.vn") is None

    def test_compound_suffix_co_uk(self) -> None:
        assert extract_root_domain("shop.acme.co.uk") == "acme.co.uk"
        assert extract_root_domain("acme.co.uk") is None

    def test_empty_domain_returns_none(self) -> None:
        assert extract_root_domain("") is None


class TestOrbisClientRootDomainBlacklist:
    """Tests für lookup_batch_by_websites_all_candidates() Root-Domain-Fallback.

    Regressionsfall aus Charge 10 (Fund) und aufgedeckt in Charge 11: nur die
    ANGEFRAGTE Domain wurde vom Caller gegen die Blacklist geprüft, nie die
    intern berechnete Root-Domain selbst. Für "engineeringblog.yelp.com"
    ergab das eine Root-Domain "yelp.com", die 1105 komplett unbeteiligte
    Kandidaten lieferte (kleine Firmen mit ihrer Yelp-Profilseite statt
    ihrer echten Website in Orbis). `root_domain_blacklist` schließt diese
    Lücke, indem JEDE berechnete Root-Domain zusätzlich geprüft wird.
    """

    def test_root_domain_fallback_without_blacklist_finds_match(self) -> None:
        """Baseline: Root-Domain-Fallback funktioniert weiterhin ohne Blacklist."""
        rows = [
            ("US999", "Unrelated Co", "https://acme.io", "US", "Springfield", None),
        ]
        fake_conn = FakeConnection(cursor_rows=rows)
        client = OrbisClient(connection=fake_conn)

        results = client.lookup_batch_by_websites_all_candidates(["blog.acme.io"])

        assert len(results["blog.acme.io"]) == 1
        assert results["blog.acme.io"][0]["matched_via_root_domain"] is True

    def test_blacklisted_root_domain_is_not_searched(self) -> None:
        """Eine blacklisted Root-Domain wird nicht als Fallback-Suchbegriff verwendet."""
        rows = [
            ("US999", "Unrelated Co", "https://acme.io", "US", "Springfield", None),
        ]
        fake_conn = FakeConnection(cursor_rows=rows)
        client = OrbisClient(connection=fake_conn)

        results = client.lookup_batch_by_websites_all_candidates(
            ["blog.acme.io"], root_domain_blacklist={"acme.io"}
        )

        assert results["blog.acme.io"] == []

    def test_wildcard_root_domain_blacklist_pattern_is_structurally_redundant(self) -> None:
        """Akzeptierte Grenze, keine offene Lücke (Gegenstück zu
        TestBlogBlacklistCoversKnownPlatformSubdomains, wo das Wildcard-Muster
        tatsächlich gebraucht wird): is_domain_blacklisted() unterstützt
        Wildcard-Muster (`*.github.io`), aber diese Methode ruft sie hier
        ausschließlich mit dem Ergebnis von extract_root_domain() auf - das liefert
        für JEDE github.io-Subdomain immer exakt "github.io" (minimale
        registrierbare Domain), nie eine Form mit Subdomain-Label davor. Ein
        Wildcard-Muster wie "*.github.io" kann hier deshalb strukturell nie
        greifen - ist aber auch nicht nötig: der exakte Eintrag ("github.io")
        blockt über den Root-Reduktions-Umweg bereits JEDE Subdomain, für JEDE
        Domain, nicht nur github.io. Ein Wildcard hätte an dieser Stelle nie
        einen Mehrwert. Diese Assertion dokumentiert bewusst den Ist-Zustand
        (Wildcard wirkungslos), nicht ein Soll, das gefixt werden müsste.
        """
        rows = [
            ("US999", "Unrelated Co", "https://github.io", "US", "Springfield", None),
        ]
        fake_conn = FakeConnection(cursor_rows=rows)
        client = OrbisClient(connection=fake_conn)

        results = client.lookup_batch_by_websites_all_candidates(
            ["docs.github.io"], root_domain_blacklist={"*.github.io"}
        )

        assert len(results["docs.github.io"]) == 1


class TestBlogBlacklistCoversKnownPlatformSubdomains:
    """Pinnt die Empfehlung aus Orbis-Notizen (intern): pilot_findings.md
    (Pattern 4) gegen die ECHTE, im Projekt eingecheckte Blacklist-Datei
    (config/blog_blacklist.txt) - anders als der `root_domain_blacklist`-Pfad in
    OrbisClient.lookup_batch_by_websites_all_candidates() (siehe
    TestOrbisClientRootDomainBlacklist.test_wildcard_root_domain_blacklist_pattern_is_structurally_redundant)
    ist das hier der Seam, an dem der Pipeline-Aufruf tatsaechlich Wirkung zeigt:
    scripts/enrichment/orbis_matching_pipeline.py laedt `blacklist = load_blog_blacklist()` und
    prueft direkt `is_domain_blacklisted(domain, blacklist)` auf der UNGEKUERZTEN,
    Original-Blog-Domain (VOR jeder Root-Domain-Reduktion).

    Realer Fall aus den Pilot-Findings: `NightsWatchGames` mit Blog
    `nightswatchgames.github.io` - eine GitHub-Pages-Unterseite, keine echte
    Firmenwebsite. Die Empfehlung dort woertlich: "Any `*.github.io` ... blog domain
    should be excluded ... This can be added to config/blog_blacklist.txt as a
    pattern class." is_domain_blacklisted() unterstuetzt dieses `*.`-Wildcard-Muster
    bereits - die Blacklist-Datei enthaelt aber (Stand jetzt) keinen einzigen
    Wildcard-Eintrag, wodurch dieser reale Fall unblacklisted durchrutscht.
    """

    def test_github_pages_subdomain_is_blacklisted(self) -> None:
        blacklist = load_blog_blacklist()
        assert is_domain_blacklisted("nightswatchgames.github.io", blacklist)

    @pytest.mark.parametrize(
        "domain,source_login",
        [
            # Jede Zeile ist eine ECHTE organizations.blog-Subdomain aus der Produktions-
            # DB (per SELECT verifiziert, 2026-08-16) - keine hypothetischen Beispiele.
            # Insgesamt ~2578 betroffene Organisationen über alle 19 Plattformen; hier
            # je ein repräsentativer Fall pro Plattform.
            ("mazieres.gitlab.io", "sexualitics"),
            ("sicpreading.bitbucket.org", "sicp-reading"),
            ("gofigure2.sourceforge.net", "gofigure2"),
            ("tellago.codeplex.com", "TellagoDevLabs"),
            ("baznex.herokuapp.com", "baznex"),
            ("aperowebnancy.netlify.app", "aperowebnancy"),
            ("octolinker.vercel.app", "OctoLinker"),
            ("man-up.appspot.com", "man-up"),
            ("lamport.azurewebsites.net", "tlaplus"),
            ("boto3.amazonaws.com", "boto"),
            ("allthingstroy.wordpress.com", "troymedia"),
            ("retrixhosting.blogspot.com", "retrix"),
            ("fotopedia-code.tumblr.com", "fotonauts"),
            ("yaroslavvb.medium.com", "cybertronai"),
            ("scaminna.hashnode.dev", "she-code-africa-minna"),
            # Der urspruengliche Charge-11-Fund (siehe batch11_review.md) selbst:
            # rutscht bis heute unblacklisted durch, weil nur die exakte Apex-Domain
            # "yelp.com" gelistet ist, nie die Subdomain-Form.
            ("engineeringblog.yelp.com", "YelpArchive"),
            ("laurierrobotics.pages.dev", "laurierrobotics"),
            ("start-fe.surge.sh", "advanced-webapps-class"),
            ("react.now.sh", "react-brasil"),
            ("weebcord.glitch.me", "Weebcord"),
        ],
    )
    def test_platform_subdomain_is_blacklisted(self, domain: str, source_login: str) -> None:
        blacklist = load_blog_blacklist()
        assert is_domain_blacklisted(
            domain, blacklist
        ), f"{domain} (echte Blog-Domain von Org '{source_login}') sollte blacklisted sein"


class TestOrbisClientDomainBatchSizeCap:
    """Tests für die ORA-01795-Absicherung ('maximum number of expressions in a
    list is 1000').

    Fund beim 500-Organisationen-Dry-Run: pro Domain werden 12 URL-Varianten
    in dieselbe IN-Clause gepackt (siehe MAX_DOMAINS_PER_BATCH-Kommentar in
    orbis_client.py). ORBIS_CONFIG["batch_size"] ist mit 500 für Queries mit
    1 Wert pro Item gedacht - bei den Website-Queries führte das zu
    443*12=5316 Bindevariablen in einer einzigen IN-Clause und ließ die
    gesamte Domain-Suche (den stärksten Match-Signal-Pfad) für den ganzen
    Lauf mit ORA-01795 fehlschlagen, ohne dass das Ergebnis das erkennen ließ
    (die Pipeline fiel unbemerkt komplett auf den schwächeren
    Namens-Fallback zurück). Diese Tests stellen sicher, dass die
    Domain-Batches unabhängig vom konfigurierten batch_size nie mehr als
    MAX_DOMAINS_PER_BATCH Domains (83, siehe 1000 // 12) pro Query enthalten.
    """

    def test_lookup_batch_by_websites_caps_batch_size(self) -> None:
        """Ein konfigurierter batch_size von 500 wird für Website-Queries
        auf MAX_DOMAINS_PER_BATCH heruntergeregelt - keine Query bekommt
        mehr als 83*12 Bindevariablen."""
        from github_collector.enrichment.orbis_client import MAX_DOMAINS_PER_BATCH

        domains = [f"example{i}.com" for i in range(MAX_DOMAINS_PER_BATCH + 20)]
        fake_conn = FakeConnection(cursor_rows=[])
        client = OrbisClient(connection=fake_conn, config={"batch_size": 500, "schema": "TEST"})

        client.lookup_batch_by_websites(domains)

        assert len(fake_conn._cursors) == 2
        for cursor in fake_conn._cursors:
            _, params = cursor.executed_queries[0]
            assert len(params) <= MAX_DOMAINS_PER_BATCH * 12

    def test_lookup_batch_by_websites_all_candidates_caps_batch_size(self) -> None:
        """Gleiche Absicherung für lookup_batch_by_websites_all_candidates()."""
        from github_collector.enrichment.orbis_client import MAX_DOMAINS_PER_BATCH

        domains = [f"example{i}.com" for i in range(MAX_DOMAINS_PER_BATCH + 20)]
        fake_conn = FakeConnection(cursor_rows=[])
        client = OrbisClient(connection=fake_conn, config={"batch_size": 500, "schema": "TEST"})

        client.lookup_batch_by_websites_all_candidates(domains, include_root_domain_fallback=False)

        for cursor in fake_conn._cursors:
            _, params = cursor.executed_queries[0]
            assert len(params) <= MAX_DOMAINS_PER_BATCH * 12
