"""
Fortschrittsanzeige für den GitHub Data Collector.

Dieses Modul bietet Funktionen zur Anzeige des Fortschritts bei der Sammlung von
GitHub-Repositories. Es ermöglicht eine einheitliche und informative Darstellung
des Sammlungsfortschritts für den Benutzer.
"""

import logging

logger = logging.getLogger(__name__)


def show_period_progress(current_period: int, total_periods: int) -> None:
    """
    Zeigt den Fortschritt der aktuellen Periode im Verhältnis zur Gesamtanzahl der Perioden an.

    Args:
        current_period: Index der aktuellen Periode (0-basiert)
        total_periods: Gesamtanzahl der Perioden
    """
    if total_periods <= 0:
        return

    # Berechne den Gesamtfortschritt
    overall_progress = min(100, ((current_period) * 100) // total_periods)

    # Zeige den Periodenfortschritt an
    print(f"\nPeriode {current_period + 1}/{total_periods} ({overall_progress}%)")


def show_repositories_found(total_count: int) -> None:
    """
    Zeigt die Anzahl der gefundenen Repositories in der aktuellen Periode an.

    Args:
        total_count: Gesamtanzahl der gefundenen Repositories
    """
    print(f"- Repositories in dieser Periode gefunden: {total_count}")


def show_period_too_large(total_count: int) -> None:
    """
    Zeigt eine Meldung an, wenn zu viele Repositories in einer Periode gefunden wurden
    und die Periode aufgeteilt werden muss.

    Args:
        total_count: Gesamtanzahl der gefundenen Repositories
    """
    message = (
        "Zu viele Repositories in der Periode "
        f"({total_count}). Teile die Periode in kleinere Zeitabschnitte auf."
    )
    logger.warning(message)
    print(f"\n{message}")


def show_collection_progress(collected_count: int, total_count: int) -> None:
    """
    Zeigt den aktuellen Sammlungsfortschritt an.

    Args:
        collected_count: Anzahl der bereits gesammelten Repositories
        total_count: Gesamtanzahl der zu sammelnden Repositories
    """
    if total_count <= 0:
        return

    progress_percent = round(collected_count * 100 / total_count, 1)
    print(f"- {collected_count}/{total_count} Repositories ({progress_percent}%)")


def show_collection_complete(total_count: int) -> None:
    """
    Zeigt eine Meldung an, wenn alle Repositories einer Periode erfolgreich gesammelt wurden.

    Args:
        total_count: Gesamtanzahl der gesammelten Repositories
    """
    print(f"- {total_count}/{total_count} Repositories (100%)")
    logger.info(f"Alle {total_count} Repositories in dieser Periode wurden erfolgreich gesammelt.")


def show_api_limit_reached() -> None:
    """Zeigt eine Meldung an, wenn die GitHub API-Beschränkung erreicht wurde."""
    logger.warning(
        "GitHub API-Beschränkung erreicht: Maximal 1000 Ergebnisse (10 Seiten) "
        "können abgerufen werden."
    )
    print(
        "Hinweis: GitHub API-Beschränkung erreicht. Nur die ersten 1000 "
        "Repositories konnten gesammelt werden."
    )


def show_max_repos_reached(max_repos: int) -> None:
    """
    Zeigt eine Meldung an, wenn die maximale Anzahl zu sammelnder Repositories erreicht wurde.

    Args:
        max_repos: Maximale Anzahl zu sammelnder Repositories
    """
    logger.info(f"Maximale Anzahl von Repositories erreicht: {max_repos}")
    print(f"\nMaximale Anzahl von Repositories erreicht: {max_repos}")


def show_collection_summary(total_collected: int) -> None:
    """
    Zeigt eine Zusammenfassung der gesamten Sammlung an.

    Args:
        total_collected: Gesamtanzahl der gesammelten Repositories
    """
    logger.info(f"Sammlung abgeschlossen. Insgesamt {total_collected} Repositories gesammelt.")
    print(f"\nSammlung abgeschlossen. Insgesamt {total_collected} Repositories gesammelt.")
