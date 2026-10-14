"""ccTLD-Extraktion: Country Code aus Blog-URL oder Email-Adresse ableiten.

Reine Funktionen ohne Datenbankzugriff. Die Anwendung auf die Tabellen
`organizations` und `contributors` erfolgt in scripts/enrichment/enrich_cctld.py.
"""

from typing import Optional, Tuple

import pycountry

# ============================================================
# TLD-KLASSIFIKATION
# ============================================================

# Kategorie B: Gefilterte TLDs (technisch ISO-Codes, aber de facto generisch)
FILTERED_TLDS = frozenset(
    {
        "io",
        "co",
        "ai",
        "me",
        "gg",
        "sh",
        "cc",
        "tv",
        "fm",
        "am",
        "ws",
        "to",
        "ly",
        "gl",
        "im",
        "nu",
        "la",
        "mu",
        "sc",
        "sx",
        "tc",
        "vg",
        "vc",
        "je",
        "ky",
        "ms",
        "as",
        "mp",
        "gu",
        "pr",
        "vi",
        "gd",
        "bz",
        "eu",
        # Kostenlose bzw. sehr billige Endungen ohne Landesbezug (Freenom u. a.)
        "tk",
        "ml",
        "ga",
        "cf",
        "gq",
        "pw",
        # Überwiegend Wortspiel-Domains (frida.re, hex.pm). .rs (Serbien, oft Rust-Projekte)
        # und .is (Island) bleiben bewusst gewertet, sie gelten als unsicher (Doku).
        "re",
        "pm",
        "tf",
        "st",
    }
)

# Dienste-Domains: Die Endung gehört zu einem Land, die Adresse aber zu einem weltweit
# genutzten Dienst (Messung 2026-10-14: linktr.ee bei 2.173 von 2.373 EE-Konten,
# notion.so bei 165 von 313 SO-Konten, youtu.be bei 135 von 867 BE-Konten).
# Gilt auch für Unterdomains (user.notion.so), nicht für fremde Domains mit gleichem
# Ende (mynotion.so).
SERVICE_DOMAINS = frozenset(
    {
        "linktr.ee",
        "buymeacoff.ee",
        "coff.ee",
        "lin.ee",
        "notion.so",
        "youtu.be",
    }
)

# Generische TLDs (>2 Zeichen) — nicht erschöpfend, aber die häufigsten
GENERIC_TLDS = frozenset(
    {
        "com",
        "org",
        "net",
        "dev",
        "app",
        "xyz",
        "tech",
        "info",
        "cloud",
        "network",
        "finance",
        "edu",
        "gov",
        "mil",
        "int",
        "arpa",
    }
)

# Kategorie C: Compound-TLDs → Country Code Mapping
COMPOUND_TLD_MAP = {
    "co.uk": "GB",
    "org.uk": "GB",
    "ac.uk": "GB",
    "net.uk": "GB",
    "com.br": "BR",
    "org.br": "BR",
    "com.au": "AU",
    "net.au": "AU",
    "org.au": "AU",
    "co.jp": "JP",
    "or.jp": "JP",
    "ne.jp": "JP",
    "co.kr": "KR",
    "or.kr": "KR",
    "com.cn": "CN",
    "net.cn": "CN",
    "org.cn": "CN",
    "com.ar": "AR",
    "com.mx": "MX",
    "com.tr": "TR",
    "co.in": "IN",
    "co.za": "ZA",
    "com.sg": "SG",
    "co.nz": "NZ",
    "co.il": "IL",
    "com.ua": "UA",
    "com.pk": "PK",
    "com.ng": "NG",
    "com.tw": "TW",
    "com.my": "MY",
    "com.ph": "PH",
    "com.co": "CO",
    "com.pe": "PE",
    "com.uy": "UY",
    "com.ec": "EC",
    "com.ve": "VE",
    "com.gt": "GT",
    "com.do": "DO",
    "com.py": "PY",
    "com.bo": "BO",
    "com.eg": "EG",
    "co.ke": "KE",
    "co.tz": "TZ",
    "co.ug": "UG",
}

# Sonderfall: .uk → GB (ISO-3166 verwendet GB, TLD ist .uk)
TLD_SPECIAL_MAPPING = {
    "uk": "GB",
}

# Valide ISO-3166-1 alpha-2 Codes (aus pycountry)
VALID_ISO_CODES = frozenset(c.alpha_2.lower() for c in pycountry.countries)


# ============================================================
# TLD-EXTRAKTION
# ============================================================


def extract_domain_from_blog(blog: str) -> Optional[str]:
    """Extrahiere Domain aus Blog-URL.

    Beispiele:
        "https://www.firma.de/about" → "firma.de"
        "firma.de" → "firma.de"
        "http://blog.firma.co.uk/" → "blog.firma.co.uk"
    """
    if not blog or not blog.strip():
        return None

    domain = blog.strip().lower()

    # Entferne Schema
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix) :]

    # Entferne www.
    if domain.startswith("www."):
        domain = domain[4:]

    # Entferne Pfad, Query, Fragment
    domain = domain.split("/")[0]
    domain = domain.split("?")[0]
    domain = domain.split("#")[0]

    # Entferne Port
    domain = domain.split(":")[0]

    # Entferne trailing dots
    domain = domain.rstrip(".")

    if not domain or "." not in domain:
        return None

    # Prüfe auf IP-Adresse
    parts = domain.split(".")
    if all(p.isdigit() for p in parts):
        return None

    return domain


def extract_domain_from_email(email: str) -> Optional[str]:
    """Extrahiere Domain aus Email-Adresse.

    Beispiele:
        "info@firma.de" → "firma.de"
        "user@mail.firma.co.uk" → "mail.firma.co.uk"
    """
    if not email or not email.strip():
        return None

    email = email.strip().lower()

    # GitHub noreply filtern
    if "users.noreply.github.com" in email:
        return None

    # Ungültig: mehrere @
    if email.count("@") != 1:
        return None

    _, domain = email.split("@")
    domain = domain.rstrip(".")

    if not domain or "." not in domain:
        return None

    return domain


def domain_to_country_code(domain: str) -> Optional[str]:
    """Leite Country Code aus Domain ab.

    Prüft in dieser Reihenfolge:
    0. Dienste-Domains (linktr.ee, notion.so, …) → None
    1. Compound-TLD (.co.uk → GB)
    2. Spezial-Mapping (.uk → GB)
    3. Einfache TLD gegen Filter und ISO-Validierung

    Returns:
        ISO-2 Country Code (uppercase) oder None
    """
    if not domain:
        return None

    # 0. Dienste-Domains und ihre Unterdomains
    if any(domain == d or domain.endswith("." + d) for d in SERVICE_DOMAINS):
        return None

    parts = domain.split(".")

    # 1. Compound-TLD prüfen (letzte zwei Segmente)
    if len(parts) >= 3:
        compound = f"{parts[-2]}.{parts[-1]}"
        if compound in COMPOUND_TLD_MAP:
            return COMPOUND_TLD_MAP[compound]

    # 2. Einfache TLD (letztes Segment)
    tld = parts[-1]

    # Generische TLDs filtern
    if tld in GENERIC_TLDS:
        return None

    # Gefilterte Länder-TLDs (de facto generisch)
    if tld in FILTERED_TLDS:
        return None

    # Spezial-Mapping
    if tld in TLD_SPECIAL_MAPPING:
        return TLD_SPECIAL_MAPPING[tld]

    # ISO-Validierung: muss 2 Buchstaben sein und gültiger ISO-Code
    if len(tld) != 2:
        return None

    if tld not in VALID_ISO_CODES:
        return None

    return tld.upper()


def extract_cctld(blog: Optional[str], email: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Extrahiere Country Code aus Blog und/oder Email.

    Returns:
        (country_code, source) wobei source in {'blog', 'email', 'both', None}
    """
    blog_domain = extract_domain_from_blog(blog) if blog else None
    email_domain = extract_domain_from_email(email) if email else None

    blog_cc = domain_to_country_code(blog_domain) if blog_domain else None
    email_cc = domain_to_country_code(email_domain) if email_domain else None

    # Konfliktauflösung
    if blog_cc and email_cc:
        if blog_cc == email_cc:
            return blog_cc, "both"
        else:
            # Blog hat Vorrang
            return blog_cc, "blog"
    elif blog_cc:
        return blog_cc, "blog"
    elif email_cc:
        return email_cc, "email"
    else:
        return None, None
