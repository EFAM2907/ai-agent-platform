"""
Circuit breaker por proveedor.

Evita seguir mandando requests a un proveedor que ya sabemos que está
caído -- en vez de esperar a que cada llamada individual falle de
nuevo (con su propio costo de latencia y reintentos), lo "abrimos" y
fallamos rápido hasta que pase un tiempo de enfriamiento.

Estados:
  CLOSED    -> comportamiento normal, todas las llamadas pasan.
  OPEN      -> se alcanzó el umbral de fallos; se rechaza de
               inmediato sin llamar al proveedor, hasta que pase
               recovery_timeout_seconds.
  HALF_OPEN -> pasó el tiempo de enfriamiento; se deja pasar UNA
               llamada de prueba. Si tiene éxito, vuelve a CLOSED.
               Si falla, vuelve a OPEN y reinicia el temporizador.
"""

from __future__ import annotations

import time
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """Se lanza cuando se intenta llamar con el circuito abierto."""


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout_seconds = recovery_timeout_seconds
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> CircuitState:
        """Recalcula el estado antes de devolverlo: si está OPEN y ya
        pasó el tiempo de enfriamiento, transiciona a HALF_OPEN aquí
        mismo (transición perezosa, no requiere un timer de fondo)."""
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self._recovery_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
        return self._state

    def allow_request(self) -> bool:
        return self.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1

        if self.state == CircuitState.HALF_OPEN:
            # La llamada de prueba falló: vuelve a abrir y reinicia
            # el temporizador de enfriamiento.
            self._open()
            return

        if self._consecutive_failures >= self._failure_threshold:
            self._open()

    def _open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()