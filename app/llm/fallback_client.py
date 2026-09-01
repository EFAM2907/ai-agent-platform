"""
FallbackLLMClient: orquesta varios LLMClient (uno por proveedor) en
orden de prioridad, con un circuit breaker independiente por
proveedor.

Vive separado de LLMClient a propósito: LLMClient ya tiene su propia
responsabilidad (retries + reparación de salida estructurada para UN
proveedor). Esta clase agrega una capa encima: "si el proveedor A
completo está caído, prueba con B, y no insistas con A si ya sabemos
que está caído".
"""

from __future__ import annotations

import logging

from app.llm.circuit_breaker import CircuitBreaker
from app.llm.client import LLMClient
from app.llm.errors import AllProvidersFailedError, LLMError
from app.llm.schemas import LLMRequest, LLMResponse

logger = logging.getLogger(__name__)


class FallbackLLMClient:
    def __init__(
        self,
        clients: list[LLMClient],
        *,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
    ) -> None:
        if not clients:
            raise ValueError("FallbackLLMClient necesita al menos un LLMClient")

        self._entries: list[tuple[LLMClient, CircuitBreaker]] = [
            (
                client,
                CircuitBreaker(
                    failure_threshold=failure_threshold,
                    recovery_timeout_seconds=recovery_timeout_seconds,
                ),
            )
            for client in clients
        ]

    async def generate(self, request: LLMRequest) -> LLMResponse:
        attempts: list[LLMError] = []

        for client, breaker in self._entries:
            provider_name = client.provider_name

            if not breaker.allow_request():
                logger.info(
                    "Circuito abierto para %s, se salta sin intentar.",
                    provider_name,
                )
                continue

            try:
                response = await client.generate(request)
            except LLMError as exc:
                breaker.record_failure()
                attempts.append(exc)
                logger.warning(
                    "Proveedor %s falló, probando siguiente. Error: %s",
                    provider_name,
                    exc,
                )
                continue

            breaker.record_success()
            return response

        raise AllProvidersFailedError(
            "Todos los proveedores fallaron o tienen el circuito abierto",
            attempts=attempts,
        )