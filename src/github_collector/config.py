"""Zentrale Konfiguration für GitHub Data Collector.

Diese Datei implementiert das zentrale Konfigurationsmanagement für den GitHub Data Collector.
Alle Konfigurationswerte werden aus Umgebungsvariablen geladen, mit sinnvollen Standardwerten.

Best Practices:
- Typenhinweise für bessere IDE-Unterstützung
- Validierung der Konfiguration beim Import
- Gruppierung verwandter Einstellungen
- Dokumentierte Standardwerte
- Einheitliche Namenskonvention (SCREAMING_SNAKE_CASE für Konstanten)
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Basis-Verzeichnisstruktur
# -------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Lade .env aus dem Projektroot, unabhängig vom aktuellen Arbeitsverzeichnis
_dotenv_path = BASE_DIR / ".env"
if _dotenv_path.exists():
    load_dotenv(_dotenv_path, override=False)

# Basis-Verzeichnisstruktur
# -------------------------
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
LOG_DIR = Path(os.getenv("LOG_DIR", BASE_DIR / "logs"))
CACHE_DIR = Path(os.getenv("CACHE_DIR", DATA_DIR / "cache"))
EXPORT_DIR = Path(os.getenv("EXPORT_DIR", DATA_DIR / "exports"))

# Erstelle benötigte Verzeichnisse
for directory in [DATA_DIR, LOG_DIR, CACHE_DIR, EXPORT_DIR]:
    directory.mkdir(exist_ok=True, parents=True)

# Datenbank-Konfiguration
# ----------------------
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "database": os.getenv("DB_NAME", "github_data"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "pool_size": int(os.getenv("DB_POOL_SIZE", "20")),
    "pool_timeout": int(os.getenv("DB_POOL_TIMEOUT", "30")),
    "pool_recycle": int(os.getenv("DB_POOL_RECYCLE", "3600")),  # 1 Stunde
}

# Konstruiere Database URL
DB_URL = os.getenv(
    "DATABASE_URL",
    f"mysql+mysqlconnector://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
    f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}",
)

# GitHub API Konfiguration
# -----------------------
GITHUB_API = {
    "url": os.getenv("GITHUB_API_URL", "https://api.github.com"),
    "version": os.getenv("GITHUB_API_VERSION", "2022-11-28"),
    "rate_limit": int(os.getenv("GITHUB_RATE_LIMIT", "5000")),
    "rate_reset": int(os.getenv("GITHUB_RATE_RESET", "3600")),  # 1 Stunde
    "search_max_results": int(os.getenv("GITHUB_MAX_RESULTS", "1000")),
    "search_page_size": int(os.getenv("GITHUB_PAGE_SIZE", "100")),
    "search_max_pages": int(os.getenv("GITHUB_MAX_PAGES", "10")),
    "timeout": int(os.getenv("GITHUB_API_TIMEOUT", "20")),  # Sekunden
    "retry_count": int(os.getenv("GITHUB_API_RETRIES", "3")),
    "rate_limit_warning_percent": int(os.getenv("GITHUB_RATE_LIMIT_WARNING_PERCENT", "10")),
    "rate_limit_critical_remaining": int(os.getenv("GITHUB_RATE_LIMIT_CRITICAL_REMAINING", "50")),
}

# Batch-Verarbeitung
# -----------------
BATCH_CONFIG = {
    "default_size": int(os.getenv("BATCH_SIZE", "50")),
    "repository_size": int(os.getenv("REPO_BATCH_SIZE", "100")),
    "graphql_limit": int(os.getenv("GRAPHQL_HISTORY", "100")),
    "max_retries": int(os.getenv("MAX_RETRIES", "3")),
    "retry_delay": int(os.getenv("RETRY_DELAY", "5")),  # Sekunden
    "progress_interval": int(os.getenv("PROGRESS_INTERVAL", "100")),  # Anzahl Operationen
    "api_retry_min_delay": float(os.getenv("API_RETRY_MIN_DELAY", "1.0")),
    "api_retry_max_delay": float(os.getenv("API_RETRY_MAX_DELAY", "3.0")),
    "api_backoff_base": int(os.getenv("API_BACKOFF_BASE", "2")),
    "api_backoff_jitter_max": float(os.getenv("API_BACKOFF_JITTER_MAX", "1.0")),
    "api_rate_limit_poll_interval": int(os.getenv("API_RATE_LIMIT_POLL_INTERVAL", "30")),
    # Neue, repository-spezifische Einstellungen
    "repository_initial_period_days": int(os.getenv("REPOSITORY_INITIAL_PERIOD_DAYS", "30")),
    "repository_rate_limit_sleep_seconds": int(
        os.getenv("REPOSITORY_RATE_LIMIT_SLEEP_SECONDS", "60")
    ),
    "repository_api_error_sleep_seconds": int(
        os.getenv("REPOSITORY_API_ERROR_SLEEP_SECONDS", "10")
    ),
}

# Oracle Orbis Konfiguration
# -------------------------
ORBIS_CONFIG = {
    "host": os.getenv("ORBIS_HOST", ""),
    "port": int(os.getenv("ORBIS_PORT", "1521")),
    "service": os.getenv("ORBIS_SERVICE", ""),
    "user": os.getenv("ORBIS_USER", ""),
    "password": os.getenv("ORBIS_PASSWORD", ""),
    "schema": os.getenv("ORBIS_SCHEMA", "ORBIS_23J"),
    "batch_size": int(os.getenv("ORBIS_BATCH_SIZE", "500")),
}

# Cache-Einstellungen
# ------------------
CACHE_CONFIG = {
    "expiry": int(os.getenv("CACHE_EXPIRY", "86400")),  # 24 Stunden
    "geocoding_file": CACHE_DIR / "geocoding_cache.json",
    "api_response_file": CACHE_DIR / "api_responses.json",
}

# Logging-Konfiguration
# -------------------
LOG_CONFIG = {
    "level": os.getenv("LOG_LEVEL", "INFO"),
    "format": os.getenv("LOG_FORMAT", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"),
    "date_format": os.getenv("LOG_DATE_FORMAT", "%Y-%m-%d %H:%M:%S"),
    "max_size": int(os.getenv("LOG_MAX_SIZE", "10485760")),  # 10MB
    "backup_count": int(os.getenv("LOG_BACKUP_COUNT", "5")),
    "files": {
        # Collection / Core Data Collection
        "repository": LOG_DIR / "collection" / "repository_collection.log",
        # Export
        "export": LOG_DIR / "export" / "export.log",
        # Geocoding
        "geocoding": LOG_DIR / "geocoding" / "geocoding.log",
        # Maintenance / Admin Tasks
        "reset": LOG_DIR / "maintenance" / "reset_and_collect.log",
        "cleanup": LOG_DIR / "maintenance" / "cleanup_orphaned_owners.log",
    },
}

# Collection State Management
# -------------------------
STATE_CONFIG = {
    "file": DATA_DIR / "collection_state.json",
    "backup_interval": int(os.getenv("STATE_BACKUP_INTERVAL", "300")),  # 5 Minuten
}

# Performance-Tracking
# ------------------
PERFORMANCE_CONFIG = {
    "enabled": os.getenv("ENABLE_PERFORMANCE_TRACKING", "true").lower()
    not in ("false", "0", "no", "off"),
    "output_dir": LOG_DIR / "performance",
}
PERFORMANCE_CONFIG["output_dir"].mkdir(exist_ok=True)

# Export-Einstellungen
# ------------------
EXPORT_CONFIG = {
    "batch_size": int(os.getenv("EXPORT_BATCH_SIZE", "1000")),
    "directory": EXPORT_DIR,
}

# Parallelisierungs-Konfiguration
# -------------------------------
MAX_WORKER_THREADS = int(os.getenv("MAX_WORKER_THREADS", "4"))
ENABLE_PARALLEL_PROCESSING = os.getenv("ENABLE_PARALLEL_PROCESSING", "false").lower() not in (
    "false",
    "0",
    "no",
    "off",
)


def validate_config() -> None:
    """Überprüft die Konfiguration auf Vollständigkeit und Gültigkeit."""
    # Überprüfe, ob mindestens eine Token-Quelle gesetzt ist
    github_api_token = os.getenv("GITHUB_API_TOKEN", "").strip()
    github_api_tokens = os.getenv("GITHUB_API_TOKENS", "").strip()
    if not github_api_token and not github_api_tokens:
        raise ValueError(
            "Mindestens ein GitHub API Token muss über GITHUB_API_TOKEN oder "
            "GITHUB_API_TOKENS gesetzt sein."
        )

    # Überprüfe Bereichsgrenzen
    if BATCH_CONFIG["default_size"] < 1:
        raise ValueError("BATCH_SIZE muss mindestens 1 sein")

    if GITHUB_API["search_page_size"] > 100:
        raise ValueError("GITHUB_PAGE_SIZE darf maximal 100 sein (GitHub API Limit)")

    # Überprüfe API Timeout und Retry-Einstellungen
    if GITHUB_API["timeout"] < 1:
        raise ValueError("GITHUB_API_TIMEOUT muss mindestens 1 Sekunde sein")

    if GITHUB_API["retry_count"] < 0:
        raise ValueError("GITHUB_API_RETRIES darf nicht negativ sein")

    # Überprüfe Parallelisierungs-/Thread-Einstellungen
    if MAX_WORKER_THREADS < 1:
        raise ValueError("MAX_WORKER_THREADS muss mindestens 1 sein")

    # Überprüfe Verzeichnisberechtigungen
    for directory in [DATA_DIR, LOG_DIR, CACHE_DIR, EXPORT_DIR]:
        if not os.access(directory, os.W_OK):
            raise PermissionError(f"Keine Schreibrechte für Verzeichnis: {directory}")


# Validiere Konfiguration beim Import
validate_config()
