"""
LLMClient: la única fachada que el resto de la app debe usar.

Nadie fuera de app/llm/ debe importar un provider concreto
(OpenAIProvider, etc.) directamente. Todo pasa por aquí, para que
routing, retries, fallback y salida estructurada entre proveedores
sean invisibles para RAG, agentes y orquestador.
"""

from __future__ import annotations

import asyncio
import json
import random

import jsonschema

from app.llm.base import LLMProvider
from app.llm.cost_logger import CostLogger
from app.llm.tracing import LangfuseTracer
from app.llm.errors import (
    InvalidResponseError,
    LLMError,
    ProviderError,
    RateLimitError,
)
from app.llm.errors import TimeoutError_ as LLMTimeoutError
from app.llm.schemas import LLMRequest, LLMResponse, Message, Role

# Errores que vale la pena reintentar: son transitorios por naturaleza.
# ContentFilterError e InvalidResponseError NO están aquí a propósito:
# reintentar una respuesta bloqueada por contenido o mal formada no
# la va a arreglar por sí sola sin un prompt distinto (ver el loop de
# reparación de salida estructurada más abajo, que sí cambia el prompt).
_RETRYABLE_ERRORS = (RateLimitError, LLMTimeoutError, ProviderError)


class LLMClient:
    """Fachada con retries+backoff/jitter y reparación de salida
    estructurada sobre un LLMProvider.

    El loop de reparación vive aquí, no en cada provider, porque es
    independiente de si el proveedor soporta JSON mode/function
    calling nativo: si de todos modos devuelve algo que no valida
    contra el schema, este es el mecanismo de última línea que lo
    corrige o falla explícitamente.
    """

    def __init__(
        self,
        provider: LLMProvider,
        *,
        max_retries: int = 3,
        base_delay_seconds: float = 1.0,
        max_delay_seconds: float = 20.0,
        max_structured_repair_attempts: int = 2,
        cost_logger: CostLogger | None = None,
        tracer: LangfuseTracer | None = None,
    ) -> None:
        self._provider = provider
        self._max_retries = max_retries
        self._base_delay_seconds = base_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._max_structured_repair_attempts = max_structured_repair_attempts
        self._cost_logger = cost_logger
        self._tracer = tracer

    @property
    def provider_name(self) -> str:
        """Nombre del proveedor detrás de este cliente, para logging
        y para que orquestadores externos (ej. FallbackLLMClient)
        no necesiten tocar el atributo privado _provider."""
        return self._provider.name

    async def generate(self, request: LLMRequest) -> LLMResponse:
        response = await self._generate_with_retries(request)

        if request.response_schema is not None:
            response = await self._ensure_structured_output(request, response)

        return response

    async def complete(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        model: str = "gpt-4o-mini",
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_schema: dict | None = None,
        tenant_id: str | None = None,
        request_tag: str | None = None,
    ) -> LLMResponse:
        """Atajo para generaciones simples, sin armar un LLMRequest a
        mano. Internamente arma el request y llama a generate() — no
        duplica ninguna lógica de retries ni de reparación."""
        messages = []
        if system_prompt:
            messages.append(Message(role=Role.SYSTEM, content=system_prompt))
        messages.append(Message(role=Role.USER, content=prompt))

        request = LLMRequest(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_schema=response_schema,
            tenant_id=tenant_id,
            request_tag=request_tag,
        )
        return await self.generate(request)

    # -- Reintentos por fallos transitorios ---------------------------------

    async def _generate_with_retries(self, request: LLMRequest) -> LLMResponse:
        last_error: LLMError | None = None

        for attempt in range(self._max_retries + 1):
            generation = (
                self._tracer.start_generation(request, self.provider_name)
                if self._tracer is not None
                else None
            )

            try:
                response = await self._provider.generate(request)
            except _RETRYABLE_ERRORS as exc:
                last_error = exc

                if generation is not None:
                    self._tracer.end_generation_error(generation, exc)

                if attempt == self._max_retries:
                    break

                delay = self._compute_delay(exc, attempt)
                await asyncio.sleep(delay)
                continue

            if generation is not None:
                self._tracer.end_generation_success(generation, response)

            self._log_cost(request, response)
            return response

        assert last_error is not None
        raise last_error

    def _log_cost(self, request: LLMRequest, response: LLMResponse) -> None:
        """Registra cada llamada real al proveedor que tuvo éxito --
        incluidas las del loop de reparación, porque también
        consumen tokens aunque el JSON haya salido inválido. No hace
        nada si no se inyectó un CostLogger (uso opcional, por
        ejemplo en tests)."""
        if self._cost_logger is None:
            return

        self._cost_logger.log(
            response,
            tenant_id=request.tenant_id,
            request_tag=request.request_tag,
        )

    def _compute_delay(self, error: LLMError, attempt: int) -> float:
        """Backoff exponencial con jitter completo, respetando
        retry_after del proveedor cuando existe (ej. 429 con header)."""
        if isinstance(error, RateLimitError) and error.retry_after is not None:
            return error.retry_after

        exponential = min(
            self._base_delay_seconds * (2**attempt),
            self._max_delay_seconds,
        )
        return random.uniform(0, exponential)

    # -- Loop de reparación de salida estructurada ---------------------------

    async def _ensure_structured_output(
        self, original_request: LLMRequest, response: LLMResponse
    ) -> LLMResponse:
        schema = original_request.response_schema
        assert schema is not None

        current_request = original_request
        current_response = response
        last_error_message = ""

        for repair_attempt in range(self._max_structured_repair_attempts + 1):
            try:
                parsed = json.loads(current_response.content)
                jsonschema.validate(instance=parsed, schema=schema)
            except (json.JSONDecodeError, jsonschema.ValidationError) as exc:
                last_error_message = str(exc)

                if repair_attempt == self._max_structured_repair_attempts:
                    raise InvalidResponseError(
                        "La salida no cumplió el schema tras "
                        f"{self._max_structured_repair_attempts} intentos "
                        f"de reparación. Último error: {last_error_message}",
                        provider=current_response.provider,
                    ) from exc

                current_request = self._build_repair_request(
                    current_request, current_response.content, last_error_message
                )
                current_response = await self._generate_with_retries(current_request)
                continue

            current_response.parsed = parsed
            return current_response

        # Inalcanzable: el for siempre retorna o lanza en la última vuelta.
        raise AssertionError("unreachable")

    @staticmethod
    def _build_repair_request(
        request: LLMRequest, malformed_content: str, error_message: str
    ) -> LLMRequest:
        """Arma una nueva request agregando la respuesta rota del
        modelo y el error exacto, pidiéndole que corrija — en vez de
        repetir el prompt original a ciegas y esperar suerte."""
        repair_messages = [
            *request.messages,
            Message(role=Role.ASSISTANT, content=malformed_content),
            Message(
                role=Role.USER,
                content=(
                    "Tu respuesta anterior no es JSON válido según el "
                    f"schema requerido. Error: {error_message}\n\n"
                    "Responde de nuevo con ÚNICAMENTE un JSON válido que "
                    "cumpla el schema, sin texto adicional antes o "
                    "después."
                ),
            ),
        ]
        return request.model_copy(update={"messages": repair_messages})