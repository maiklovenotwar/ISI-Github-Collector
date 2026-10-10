"""Hilfsfunktionen für den sicheren Umgang mit Secrets (Tokens, DB-URLs).

Die SecretManager-Klasse kapselt die Logik zum Laden und Validieren von
GitHub-API-Tokens sowie zum Maskieren von Passwörtern in Datenbank-URLs
für Logging-Zwecke.
"""

import getpass
import os
import re
from typing import List
from urllib.parse import urlparse, urlunparse

import keyring


class SecretManager:
    """Zentrale Hilfsklasse für Secret-Management.

    Verantwortlichkeiten:
    - GitHub-API-Tokens aus sicheren Quellen laden
    - Token-Formate validieren
    - Datenbank-URLs für Logging von Passwörtern bereinigen
    """

    @staticmethod
    def get_github_tokens() -> List[str]:
        """Lädt GitHub-Tokens aus sicheren Quellen.

        Priorität:
        1. Environment-Variablen (GITHUB_API_TOKENS, GITHUB_API_TOKEN)
        2. System-Keyring (Service "github_collector", User "api_token")
        3. Sichere Eingabe via getpass (interaktiv)

        Raises:
            ValueError: Wenn kein gültiges Token gefunden oder eingegeben wurde.
        """

        tokens: List[str] = []

        # 1. Environment Variables (mehrere Tokens per CSV-Liste)
        tokens_env = os.environ.get("GITHUB_API_TOKENS", "")
        if tokens_env:
            tokens.extend([t.strip() for t in tokens_env.split(",") if t.strip()])

        # 2. Einzelnes Token in Environment
        single_token = os.environ.get("GITHUB_API_TOKEN")
        if single_token:
            tokens.insert(0, single_token.strip())

        if tokens:
            # Optional: Formate grob validieren, aber nicht zu streng filtern
            valid_tokens = [t for t in tokens if SecretManager.validate_github_token(t)]
            if valid_tokens:
                return valid_tokens
            # Falls alle Tokens ungültig wirken, lieber explizit scheitern

        # 3. Keyring
        try:
            stored = keyring.get_password("github_collector", "api_token")
        except Exception:
            stored = None

        if stored and SecretManager.validate_github_token(stored):
            return [stored]

        # 4. Sichere Eingabe (interaktiv)
        try:
            token = getpass.getpass("GitHub API Token: ")
        except (EOFError, KeyboardInterrupt):  # z.B. in nicht-interaktiven Umgebungen
            token = ""

        if token and SecretManager.validate_github_token(token):
            # Versuche, das Token im Keyring zu speichern (Best Effort)
            try:
                keyring.set_password("github_collector", "api_token", token)
            except Exception:
                # Kein Hard-Fail, wenn Keyring nicht funktioniert
                pass
            return [token]

        # Benutzerfreundliche Fehlermeldung mit klarer Handlungsanweisung
        raise ValueError(
            "Kein GitHub-Token gefunden. Bitte setzen Sie die Umgebungsvariable "
            "GITHUB_API_TOKEN oder GITHUB_API_TOKENS, oder führen Sie das Skript "
            "interaktiv aus, um es hinzuzufügen."
        )

    @staticmethod
    def validate_github_token(token: str) -> bool:
        """Validiert das Format eines GitHub Personal Access Tokens.

        Unterstützte Formate (Stand Masterplan):
        - Klassische PATs: ghp_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
        - Fine-grained PATs:
          github_pat_XXXXXXXXXXXXXXXXXXXX_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
        """

        if not token:
            return False

        patterns = [
            r"^ghp_[a-zA-Z0-9]{36}$",
            r"^github_pat_[a-zA-Z0-9]{22}_[a-zA-Z0-9]{59}$",
        ]
        return any(re.match(p, token) for p in patterns)

    @staticmethod
    def sanitize_db_url(url: str) -> str:
        """Entfernt Passwörter aus DB-URLs für Logging.

        Beispiel:
            mysql+mysqlconnector://user:pass@host:3306/db
        wird zu:
            mysql+mysqlconnector://user:****@host:3306/db

        Bei URLs ohne Passwort bleibt die Eingabe unverändert.
        """

        if not url:
            return url

        parsed = urlparse(url)
        # Nur maskieren, wenn tatsächlich Credentials enthalten sind
        if parsed.password:
            netloc = f"{parsed.username}:****@{parsed.hostname}" if parsed.username else ""
            if parsed.port:
                netloc += f":{parsed.port}"
            return urlunparse(
                (
                    parsed.scheme,
                    netloc,
                    parsed.path,
                    parsed.params,
                    parsed.query,
                    parsed.fragment,
                )
            )
        return url
