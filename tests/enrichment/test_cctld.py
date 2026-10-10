"""Unit tests for the ccTLD country-code extraction (enrichment/cctld.py)."""

import pytest

from github_collector.enrichment.cctld import (
    domain_to_country_code,
    extract_cctld,
    extract_domain_from_blog,
    extract_domain_from_email,
)


class TestExtractDomainFromBlog:
    @pytest.mark.parametrize(
        "blog,expected",
        [
            ("https://www.firma.de/about", "firma.de"),
            ("firma.de", "firma.de"),
            ("http://blog.firma.co.uk/", "blog.firma.co.uk"),
            ("HTTPS://Firma.DE:8080/path?x=1#top", "firma.de"),
            ("firma.de.", "firma.de"),
        ],
    )
    def test_normalizes_url_to_domain(self, blog: str, expected: str) -> None:
        assert extract_domain_from_blog(blog) == expected

    @pytest.mark.parametrize("blog", ["", "   ", "localhost", "192.168.0.1", "http://10.0.0.1/"])
    def test_rejects_values_without_usable_domain(self, blog: str) -> None:
        assert extract_domain_from_blog(blog) is None


class TestExtractDomainFromEmail:
    def test_returns_domain_part(self) -> None:
        assert extract_domain_from_email("Info@Firma.DE") == "firma.de"

    @pytest.mark.parametrize(
        "email",
        ["", "12345+user@users.noreply.github.com", "a@b@firma.de", "user@localhost", "firma.de"],
    )
    def test_rejects_unusable_addresses(self, email: str) -> None:
        assert extract_domain_from_email(email) is None


class TestDomainToCountryCode:
    @pytest.mark.parametrize(
        "domain,expected",
        [
            ("firma.de", "DE"),
            ("example.fr", "FR"),
            ("example.uk", "GB"),
            ("blog.firma.co.uk", "GB"),
            ("shop.example.com.br", "BR"),
        ],
    )
    def test_maps_country_tlds(self, domain: str, expected: str) -> None:
        assert domain_to_country_code(domain) == expected

    @pytest.mark.parametrize(
        "domain",
        [
            "example.com",  # generic TLD
            "example.io",  # ISO code used generically
            "example.eu",  # not a country
            "example.zz",  # two letters, not an ISO code
            "example.technology",  # unknown long TLD
        ],
    )
    def test_ignores_tlds_without_country_signal(self, domain: str) -> None:
        assert domain_to_country_code(domain) is None

    def test_compound_tld_needs_a_registrable_label(self) -> None:
        # "co.uk" alone has only two labels; the plain ".uk" rule still applies.
        assert domain_to_country_code("co.uk") == "GB"


class TestExtractCctld:
    def test_blog_only(self) -> None:
        assert extract_cctld("https://firma.de", None) == ("DE", "blog")

    def test_email_only(self) -> None:
        assert extract_cctld(None, "info@firma.fr") == ("FR", "email")

    def test_agreeing_sources_are_reported_as_both(self) -> None:
        assert extract_cctld("https://firma.de", "info@firma.de") == ("DE", "both")

    def test_blog_takes_precedence_on_conflict(self) -> None:
        assert extract_cctld("https://firma.de", "info@firma.fr") == ("DE", "blog")

    def test_email_is_used_when_blog_has_generic_tld(self) -> None:
        assert extract_cctld("https://firma.com", "info@firma.fr") == ("FR", "email")

    def test_no_country_signal(self) -> None:
        assert extract_cctld("https://firma.io", "info@gmail.com") == (None, None)
