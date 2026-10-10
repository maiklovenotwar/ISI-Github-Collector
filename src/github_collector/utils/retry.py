"""Generische Retry-Hilfsfunktionen mit exponentiellem Backoff.

Dieser Modul stellt einen Decorator ``retry_with_backoff`` bereit, der
transiente Fehler (z.B. Netzwerk- oder Datenbankfehler) durch mehrere
Wiederholungsversuche mit wachsender Wartezeit abfedern kann.

Der Decorator ist bewusst generisch gehalten und kann sowohl für
Datenbank-Operationen (z.B. SQLAlchemy ``OperationalError``) als auch
für HTTP-/API-Aufrufe (z.B. ``requests.exceptions.RequestException``)
verwendet werden.
"""

from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Any, Callable, Iterable, Optional, Tuple, Type, TypeVar, Union

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def _to_exception_tuple(
    exceptions: Optional[Union[Type[BaseException], Iterable[Type[BaseException]]]],
) -> Tuple[Type[BaseException], ...]:
    """Normalisiert die Exception-Spezifikation auf ein Tupel von Typen.

    Args:
        exceptions: Einzelner Exception-Typ oder Iterable von Exception-Typen.

    Returns:
        Tupel von Exception-Typen. Falls ``None`` übergeben wird, wird
        ``(Exception,)`` verwendet.
    """

    if exceptions is None:
        return (Exception,)

    if isinstance(exceptions, type) and issubclass(exceptions, BaseException):
        return (exceptions,)

    return tuple(exceptions)


def retry_with_backoff(  # noqa: C901
    max_attempts: int = 3,
    backoff_factor: float = 2.0,
    initial_delay: float = 1.0,
    exceptions: Optional[Union[Type[BaseException], Iterable[Type[BaseException]]]] = None,
    logger_instance: Optional[logging.Logger] = None,
) -> Callable[[F], F]:
    """Decorator für Retry-Logik mit exponentiellem Backoff.

    Beispiel:

    .. code-block:: python

        from requests.exceptions import RequestException

        @retry_with_backoff(max_attempts=3, backoff_factor=2.0, exceptions=RequestException)
        def fetch_data():
            ...

    Args:
        max_attempts: Maximale Anzahl von Versuchen (inklusive des ersten Aufrufs).
        backoff_factor: Multiplikator für die Wartezeit zwischen den Versuchen.
        initial_delay: Startverzögerung in Sekunden vor dem zweiten Versuch.
        exceptions: Exception-Typ oder Iterable von Typen, bei denen ein Retry
            versucht werden soll. Standard: ``Exception``.
        logger_instance: Optionaler Logger. Wenn ``None``, wird der Modul-Logger
            verwendet.

    Returns:
        Einen Decorator, der die Ziel-Funktion mit Retry-Logik umhüllt.
    """

    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    if backoff_factor <= 0:
        raise ValueError("backoff_factor must be > 0")
    if initial_delay < 0:
        raise ValueError("initial_delay must be >= 0")

    exception_types = _to_exception_tuple(exceptions)
    log = logger_instance or logger

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            attempt = 1
            delay = initial_delay

            while True:
                try:
                    return func(*args, **kwargs)
                except exception_types as exc:  # type: ignore[misc]
                    if attempt >= max_attempts:
                        log.error(
                            "Retry-Grenze erreicht (%s Versuche) für %s: %s",
                            max_attempts,
                            func.__name__,
                            exc,
                        )
                        raise

                    # Logge Warnung und warte vor erneutem Versuch
                    log.warning(
                        "Fehler in %s (Versuch %s/%s): %s - erneuter Versuch nach %.2fs",
                        func.__name__,
                        attempt,
                        max_attempts,
                        exc,
                        delay,
                    )

                    if delay > 0:
                        time.sleep(delay)

                    attempt += 1
                    if delay == 0:
                        # Falls initial_delay=0, starte mit 1 * backoff_factor
                        delay = backoff_factor
                    else:
                        delay *= backoff_factor

        return wrapper  # type: ignore[return-value]

    return decorator
