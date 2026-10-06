"""
LLMClient: la única fachada que el resto de la app debe usar.

Nadie fuera de app/llm/ debe importar un provider concreto
(OpenAIProvider, etc.) directamente. Todo pasa por aquí, para que
routing, fallback y salida estructurada entre proveedores sean
invisibles para RAG, agentes y orquestador.

División de responsabilidades sobre retries (deliberada, no un
descuido): los reintentos técnicos ante un proveedor (429, 5xx,
timeout transitorio) son responsabilidad de LiteLLM
(router_settings.num_retries en litellm_config.yaml), NUNCA de esta
clase. Antes este cliente reintentaba TAMBIÉN esos mismos errores por
su cuenta -- lo que significaba, bajo rate-limit sostenido, hasta
app_retries × litellm_retries llamadas reales al proveedor para una
sola generación, con su propio backoff exponencial encima del de
LiteLLM. Eso fue justo lo que convirtió una consulta simple en ~160s
de espera en producción. LLMClient ahora solo se ocupa de lo que
LiteLLM no puede resolver por sí solo: reparar una salida estructurada
que no cumple el schema pedido (que requiere cambiar el prompt, no
repetir la misma solicitud).
"""

from __future__ import annotations

import json

import jsonschema

from app.llm.base import LLMProvider
from app.llm.errors import InvalidResponseError
from app.llm.schemas import LLMRequest, LLMResponse, Message, Role
from app.llm.streaming import StreamUsageCollector


class LLMClient:
    """Fachada con reparación de salida estructurada sobre un
    LLMProvider -- los retries técnicos ante el proveedor son
    responsabilidad de LiteLLM, ver el docstring del módulo.

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
        max_structured_repair_attempts: int = 2,
    ) -> None:
        self._provider = provider
        self._max_structured_repair_attempts = max_structured_repair_attempts

    @property
    def provider_name(self) -> str:
        """Nombre del proveedor detrás de este cliente, para logging
        y para que orquestadores externos (ej. FallbackLLMClient)
        no necesiten tocar el atributo privado _provider."""
        return self._provider.name

    async def generate(self, request: LLMRequest) -> LLMResponse:
        response = await self._provider.generate(request)

        if request.response_schema is not None:
            response = await self._ensure_structured_output(request, response)

        return response

    async def generate_stream(
        self, request: LLMRequest, collector: StreamUsageCollector | None = None
    ):
        """Streams text deltas directly from the provider, for live
        display (SSE) instead of waiting for the full response.

        Deliberately NOT wrapped in the structured-output repair loop:
        repairing requires the full response before you can even
        attempt to parse it, which defeats the purpose of streaming.
        Use generate() instead when you need that guarantee; use
        generate_stream() only for plain conversational text (or text
        plus tool_calls, see `collector`) meant to be shown live.

        collector: optional StreamUsageCollector -- if passed, filled
        with the aggregated LLMResponse (usage, finish_reason, and any
        tool_calls the model requested) once the stream ends, without
        changing what gets yielded. See app.llm.tools.run_streaming_tool_loop
        for how this lets a caller detect and execute tool_calls on a
        streamed turn instead of only on generate().

        Cost y tracing no se manejan acá: el gateway LiteLLM los
        registra nativamente (ver litellm_config.yaml) para cada
        request que pasa por el proxy, sin que la app necesite
        acumular uso ni abrir observaciones propias.
        """
        if not self._provider.supports_streaming():
            raise NotImplementedError(
                f"El provider '{self.provider_name}' no soporta streaming"
            )

        async for chunk in self._provider.generate_stream(request, collector=collector):
            yield chunk

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
                current_response = await self._provider.generate(current_request)
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
