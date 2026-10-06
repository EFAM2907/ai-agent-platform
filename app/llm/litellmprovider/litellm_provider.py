"""Adaptador del LiteLLM Proxy al contrato normalizado de la aplicacion.

LiteLLM expone una API compatible con OpenAI, pero no se trata como un
``OpenAIProvider``: su nombre, URL y credencial representan al gateway. Asi los
traces y logs distinguen correctamente una llamada directa de una que paso por
el proxy.
"""

from __future__ import annotations

import json
import time

import openai
from openai import AsyncOpenAI

from app.core.config import settings
from app.llm.base import LLMProvider
from app.llm.errors import (
    ContentFilterError,
    InvalidRequestError,
    InvalidResponseError,
    ProviderError,
    RateLimitError,
)
from app.llm.errors import TimeoutError_ as LLMTimeoutError
from app.llm.schemas import LLMRequest, LLMResponse, Message, TokenUsage, ToolCall, ToolDefinition
from app.llm.streaming import StreamUsageCollector


_FINISH_REASON_MAP: dict[str, str] = {
    "stop": "stop",
    "length": "length",
    "content_filter": "content_filter",
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
}


class LiteLLMProvider(LLMProvider):
    """Cliente del gateway LiteLLM a traves de Chat Completions.

    El endpoint de chat es la parte mas estable de la superficie
    OpenAI-compatible del proxy y permite que el gateway unifique Gemini,
    OpenAI y Anthropic sin filtrar SDKs de esos proveedores a la aplicacion.
    """

    name = "litellm"

    # Sin esto, el SDK de OpenAI usa su default de 10 minutos -- si el
    # proxy LiteLLM o el proveedor detras se cuelgan (o se quedan
    # reintentando internamente, ver router_settings.num_retries en
    # litellm_config.yaml), una sola llamada de chat puede colgar el
    # request muchisimo mas de lo que cualquier usuario va a esperar.
    # 60s ya es generoso para una respuesta de chat real, incluso con
    # "thinking" activado.
    _REQUEST_TIMEOUT_SECONDS = 60.0

    def __init__(self, virtual_key: str) -> None:
        """virtual_key: SIEMPRE una virtual key -- la del tenant si ya
        fue aprovisionada, o la de emergencia
        (settings.litellm_emergency_fallback_key) si no. La master key
        nunca debe autenticar tráfico real de chat, solo llamadas admin
        (/key/generate y equivalentes, ver app.llm.litellm_admin). Cuál
        virtual key usar es responsabilidad del caller -- ver
        app.organizations.dependencies.get_tenant_llm_client."""
        self.client = AsyncOpenAI(
            base_url=settings.litellm_base_url.rstrip("/"),
            api_key=virtual_key,
            timeout=self._REQUEST_TIMEOUT_SECONDS,
        )

    def supports_structured_output(self) -> bool:
        # El proxy no puede garantizar JSON schema para todos los modelos que
        # enruta. LLMClient aplicara su loop de reparacion portable.
        return False

    def supports_streaming(self) -> bool:
        return True

    @staticmethod
    def _extra_body(request: LLMRequest) -> dict | None:
        """extra_body es el mecanismo estandar del SDK de OpenAI para
        pasar campos que no son parte de su firma tipada -- lo unico
        que reenviamos hoy es reasoning_effort (ver LLMRequest), que
        LiteLLM traduce al thinkingConfig nativo de Gemini.

        Riesgo conocido, no resuelto aca: si el modelo pedido falla y
        el router de litellm_config.yaml cae a un fallback (ver
        router_settings.fallbacks), ese modelo puede no aceptar el
        mismo valor de reasoning_effort (ej. "none" es valido para
        Gemini via LiteLLM, pero no esta confirmado que gpt-5.6-luna
        lo acepte igual desde la API real de OpenAI). No se le puso
        drop_params aca a proposito -- mismo principio que el resto
        del gateway: un parametro no soportado en una llamada de chat
        debe fallar fuerte, no ignorarse en silencio."""
        if request.reasoning_effort is None:
            return None
        return {"reasoning_effort": request.reasoning_effort}

    @staticmethod
    def _to_api_message(message: Message) -> dict:
        """Traduce un Message normalizado al dict que espera Chat
        Completions -- omite tool_calls/tool_call_id por completo
        cuando no aplican, en vez de mandarlos como None, para no
        ensuciar el payload de un mensaje de texto plano de siempre."""
        payload: dict = {"role": message.role.value, "content": message.content}
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
                    },
                }
                for tool_call in message.tool_calls
            ]
        if message.tool_call_id is not None:
            payload["tool_call_id"] = message.tool_call_id
        return payload

    @staticmethod
    def _tools_payload(tools: list[ToolDefinition] | None) -> list[dict] | None:
        """None (sin tools) se preserva tal cual -- el SDK de OpenAI
        omite el campo `tools` del request cuando su valor es None, que
        es exactamente el comportamiento de siempre para cualquier
        llamada que no pase tools (RAG, ModelRouter, etc.)."""
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]

    @staticmethod
    def _parse_tool_calls(raw_tool_calls) -> list[ToolCall] | None:
        """raw_tool_calls es message.tool_calls del SDK de OpenAI (o
        None). arguments llega como un string JSON crudo -- se parsea
        aca, no se le pasa al resto de la app como texto sin procesar,
        para que ToolCall.arguments sea siempre un dict utilizable."""
        if not raw_tool_calls:
            return None
        return [
            ToolCall(
                id=tool_call.id,
                name=tool_call.function.name,
                arguments=json.loads(tool_call.function.arguments)
                if tool_call.function.arguments
                else {},
            )
            for tool_call in raw_tool_calls
        ]

    async def generate_stream(
        self, request: LLMRequest, collector: StreamUsageCollector | None = None
    ):
        """Yields text deltas as they arrive from el proxy LiteLLM. Sin
        retries ni loop de reparacion de salida estructurada -- ver
        LLMClient.generate_stream() para el porqué.

        Tambien soporta tools: los tool_calls llegan fragmentados en
        varios chunks (el `id` y `function.name` solo en el primero
        para ese indice, `function.arguments` de a pedazos en los
        siguientes) -- se acumulan por indice en vez de yieldearse como
        texto, y solo se ensamblan en ToolCall completos al final del
        stream, adentro de collector.final_response. El texto (si lo
        hay) sí se yieldea en vivo como siempre; un turno que solo pide
        tools tipicamente no trae texto que mostrar.

        If a collector is passed, se llena con el LLMResponse agregado
        (texto completo, uso real de tokens, finish_reason, tool_calls)
        una vez termina el stream -- construido a partir de chunk.usage,
        que solo llega poblado con stream_options={"include_usage": True}.
        """
        started_at = time.perf_counter()

        stream = await self.client.chat.completions.create(
            model=request.model,
            messages=[self._to_api_message(message) for message in request.messages],
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            tools=self._tools_payload(request.tools),
            stream=True,
            stream_options={"include_usage": True},
            extra_body=self._extra_body(request),
        )

        accumulated_text: list[str] = []
        # index -> fragmentos acumulados de ese tool_call. dict, no
        # list, porque los chunks pueden (en teoria) llegar con indices
        # fuera de orden o intercalados si el modelo pide varias tools
        # a la vez.
        tool_call_fragments: dict[int, dict[str, str]] = {}
        finish_reason_raw: str | None = None
        usage = None
        response_model = request.model

        async for chunk in stream:
            if chunk.model:
                response_model = chunk.model

            # Bug conocido de LiteLLM: el chunk que trae el usage final
            # a veces también trae choices no vacío (en vez de llegar
            # solo con usage y choices=[], como en la API de OpenAI).
            # Por eso el chunk de usage se detecta por chunk.usage is
            # not None, nunca por si choices está vacío o no.
            if chunk.usage is not None:
                usage = chunk.usage

            if chunk.choices:
                choice = chunk.choices[0]
                if choice.delta and choice.delta.content:
                    accumulated_text.append(choice.delta.content)
                    yield choice.delta.content
                if choice.delta and choice.delta.tool_calls:
                    for tc_delta in choice.delta.tool_calls:
                        fragment = tool_call_fragments.setdefault(
                            tc_delta.index, {"id": "", "name": "", "arguments": ""}
                        )
                        if tc_delta.id:
                            fragment["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                fragment["name"] += tc_delta.function.name
                            if tc_delta.function.arguments:
                                fragment["arguments"] += tc_delta.function.arguments
                if choice.finish_reason:
                    finish_reason_raw = choice.finish_reason

        if collector is not None:
            collector.final_response = self._build_stream_final_response(
                usage,
                finish_reason_raw,
                "".join(accumulated_text),
                response_model,
                started_at,
                tool_call_fragments,
            )

    def _build_stream_final_response(
        self,
        usage,
        finish_reason_raw: str | None,
        full_text: str,
        model: str,
        started_at: float,
        tool_call_fragments: dict[int, dict[str, str]] | None = None,
    ) -> LLMResponse | None:
        """Best-effort: si el chunk final no trajo usage utilizable,
        devuelve None en vez de lanzar -- un stream que ya terminó
        bien para el caller no debería romperse solo porque no se
        pudo cerrar la contabilidad de costo."""
        if usage is None:
            return None

        try:
            tool_calls = None
            if tool_call_fragments:
                tool_calls = [
                    ToolCall(
                        id=fragment["id"],
                        name=fragment["name"],
                        arguments=json.loads(fragment["arguments"]) if fragment["arguments"] else {},
                    )
                    for _, fragment in sorted(tool_call_fragments.items())
                ]
            return LLMResponse(
                content=full_text,
                model=model,
                provider=self.name,
                tokens_used=TokenUsage(
                    input_tokens=usage.prompt_tokens or 0,
                    output_tokens=usage.completion_tokens or 0,
                ),
                latency_ms=(time.perf_counter() - started_at) * 1000,
                finish_reason=_FINISH_REASON_MAP.get(
                    finish_reason_raw, "stop"
                ),
                tool_calls=tool_calls,
            )
        except (AttributeError, json.JSONDecodeError):
            return None

    async def generate(self, request: LLMRequest) -> LLMResponse:
        started_at = time.perf_counter()

        try:
            response = await self.client.chat.completions.create(
                model=request.model,
                messages=[self._to_api_message(message) for message in request.messages],
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                tools=self._tools_payload(request.tools),
                extra_body=self._extra_body(request),
            )
        except openai.RateLimitError as exc:
            retry_after = getattr(exc.response, "headers", {}).get("retry-after")
            raise RateLimitError(
                str(exc),
                provider=self.name,
                original_error=exc,
                retry_after=float(retry_after) if retry_after else None,
            ) from exc
        except openai.APITimeoutError as exc:
            raise LLMTimeoutError(
                str(exc), provider=self.name, original_error=exc
            ) from exc
        except openai.BadRequestError as exc:
            if "content_filter" in str(exc).lower():
                raise ContentFilterError(
                    str(exc), provider=self.name, original_error=exc
                ) from exc
            # InvalidRequestError, NO ProviderError: un 400 (modelo sin
            # crédito, parámetro inválido, etc.) nunca se arregla
            # reintentando la misma solicitud -- ver el docstring de
            # InvalidRequestError sobre el incidente real que causó
            # este cambio (~170s de retries inútiles sobre un 400).
            raise InvalidRequestError(
                str(exc), provider=self.name, original_error=exc
            ) from exc
        except openai.APIError as exc:
            raise ProviderError(
                str(exc), provider=self.name, original_error=exc
            ) from exc

        latency_ms = (time.perf_counter() - started_at) * 1000

        try:
            choice = response.choices[0]
            content = choice.message.content or ""
            usage = response.usage
            tool_calls = self._parse_tool_calls(choice.message.tool_calls)
            # Contenido vacio es normal cuando el LLM esta pidiendo una
            # tool (no tiene nada que "decir" todavia) -- solo es un
            # error real si viene vacio Y sin tool_calls.
            if (content == "" and not tool_calls) or usage is None:
                raise ValueError("LiteLLM devolvio contenido o uso vacio")
        except (AttributeError, IndexError, ValueError, json.JSONDecodeError) as exc:
            raise InvalidResponseError(
                "No se pudo mapear la respuesta de LiteLLM a LLMResponse",
                provider=self.name,
                original_error=exc,
            ) from exc

        return LLMResponse(
            content=content,
            model=response.model or request.model,
            provider=self.name,
            tokens_used=TokenUsage(
                input_tokens=usage.prompt_tokens or 0,
                output_tokens=usage.completion_tokens or 0,
            ),
            latency_ms=latency_ms,
            finish_reason=_FINISH_REASON_MAP.get(choice.finish_reason, "stop"),
            tool_calls=tool_calls,
        )
