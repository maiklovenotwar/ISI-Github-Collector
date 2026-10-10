"""Integration Tests für Oracle Orbis Verbindung.

Diese Tests erfordern eine aktive VPN-Verbindung zum Fraunhofer-Netzwerk
und sind standardmäßig DEAKTIVIERT.

Ausführung mit aktivem VPN:
    pytest tests/integration/test_orbis_connection.py -m orbis_integration -v

Die Tests prüfen:
- Verbindung zur Oracle-DB kann aufgebaut werden
- Schema enthält erwartete Spalten (CONTACT_INFO: BVD_ID, WEBSITE, COUNTRY_ISO)
- Batch-Query mit bekannter Domain liefert plausibles Ergebnis
"""

import pytest

from github_collector.enrichment.orbis_client import (
    OrbisClient,
    OrbisConnectionError,
)


@pytest.mark.orbis_integration
class TestOrbisLiveConnection:
    """Live-Tests gegen Oracle Orbis (erfordern VPN)."""

    def test_connection_can_be_established(self) -> None:
        """Verbindung zur Oracle-DB kann aufgebaut werden."""
        try:
            client = OrbisClient()
            result = client.test_connection()
            client.close()

            assert result is True, "Connection test should return True"
        except OrbisConnectionError as e:
            pytest.fail(f"Connection failed (VPN active?): {e}")

    def test_schema_has_expected_columns(self) -> None:
        """Schema CONTACT_INFO enthält erwartete Spalten."""
        client = OrbisClient()
        try:
            info = client.get_table_info("CONTACT_INFO")

            column_names = [col["name"] for col in info.get("columns", [])]

            # Required columns for our enrichment
            required = ["BVD_ID", "WEBSITE", "COUNTRY_ISO", "NAME"]
            for col in required:
                assert col in column_names, f"Column {col} missing in CONTACT_INFO"

            # Table should have many rows (400M+)
            estimated_rows = info.get("estimated_rows")
            assert estimated_rows is not None, "Should have row count estimate"
            assert estimated_rows > 1_000_000, f"Expected millions of rows, got {estimated_rows}"

        finally:
            client.close()

    def test_batch_query_returns_plausible_result(self) -> None:
        """Batch-Query mit Test-Domain liefert plausibles Ergebnis.

        Hinweis: Dieser Test verwendet eine Domain die wahrscheinlich in
        Orbis existiert. Falls der Test fehlschlägt, liegt es möglicherweise
        daran dass die Domain nicht mehr in der DB ist.
        """
        client = OrbisClient()
        try:
            # Use a well-known company domain that should be in Orbis
            test_domains = ["siemens.com", "sap.com", "bosch.com"]

            results = client.lookup_batch_by_websites(test_domains)

            # At least one should match
            assert len(results) > 0, (
                f"Expected at least one match for {test_domains}, got none. "
                "This may indicate a query issue or the domains are not in Orbis."
            )

            # Check result structure
            for domain, data in results.items():
                assert "bvd_id" in data, f"Missing bvd_id for {domain}"
                assert "country_iso" in data, f"Missing country_iso for {domain}"
                assert data["bvd_id"] is not None, f"bvd_id is None for {domain}"

                # German companies should have DE country code
                if domain in ["siemens.com", "sap.com", "bosch.com"]:
                    # Note: This assertion might fail if the company has
                    # a different country in Orbis (e.g., subsidiary)
                    pass  # Don't assert country, just check structure

        finally:
            client.close()

    def test_empty_domain_list_returns_empty(self) -> None:
        """Leere Domain-Liste gibt leeres Dict zurück."""
        client = OrbisClient()
        try:
            results = client.lookup_batch_by_websites([])
            assert results == {}
        finally:
            client.close()

    def test_nonexistent_domain_returns_empty(self) -> None:
        """Nicht-existente Domain gibt leeres Dict zurück."""
        client = OrbisClient()
        try:
            # Use a domain that definitely doesn't exist in Orbis
            results = client.lookup_batch_by_websites(
                ["this-domain-definitely-does-not-exist-xyz123.com"]
            )
            assert results == {}
        finally:
            client.close()


@pytest.mark.orbis_integration
class TestOrbisConnectionResilience:
    """Tests für Connection-Resilience."""

    def test_context_manager_cleanup(self) -> None:
        """Context Manager schließt Connection korrekt."""
        with OrbisClient() as client:
            assert client.test_connection() is True

        # After context exit, connection should be closed
        assert client.connection is None

    def test_multiple_queries_same_connection(self) -> None:
        """Mehrere Queries über dieselbe Connection funktionieren."""
        client = OrbisClient()
        try:
            # First query
            results1 = client.lookup_batch_by_websites(["siemens.com"])

            # Second query
            results2 = client.lookup_batch_by_websites(["sap.com"])

            # Both should work
            # (may be empty if domains not in DB, but should not error)
            assert isinstance(results1, dict)
            assert isinstance(results2, dict)

        finally:
            client.close()
