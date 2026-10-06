"""
Implementación concreta de LLMProvider para Google Gemini.

Traduce entre el formato normalizado (LLMRequest/LLMResponse) y el
SDK oficial de Google: `google-genai` (paquete pip: google-genai).

IMPORTANTE: no confundir con `google-generativeai`, que es el SDK
anterior y está oficialmente deprecado por Google — este archivo usa
el reemplazo unificado y vigente.
"""

from __future__ import annotations

import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.core.config import settings
from app.llm.base import LLMProvider
from app.llm.errors import (
    ContentFilterError,
    InvalidResponseError,
    ProviderError,
    RateLimitError,
)
from app.llm.errors import TimeoutError_ as LLMTimeoutError
from app.llm.schemas import LLMRequest, LLMResponse, Role, TokenUsage
from app.llm.streaming import StreamUsageCollector

# Mapea el finish_reason nativo de Gemini (google.genai.types.FinishReason,
# un str-enum: "STOP", "MAX_TOKENS", etc.) al set fijo de LLMResponse.
# Confirmado contra los valores reales de types.FinishReason en el SDK
# instalado (no es un supuesto sin verificar).
_FINISH_REASON_MAP: dict[str, str] = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
    "SPII": "content_filter",
    "BLOCKLIST": "content_filter",
    "LANGUAGE": "stop",
    "OTHER": "stop",
    "MALFORMED_FUNCTION_CALL": "tool_calls",
    "TOO_MANY_TOOL_CALLS": "tool_calls",
    "UNEXPECTED_TOOL_CALL": "tool_calls",
}

# finish_reasons que indican bloqueo por contenido, no un fallo genérico.
_CONTENT_FILTER_REASONS = {"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "SPII", "BLOCKLIST"}


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self) -> None:
        self._client = genai.Client(api_key=settings.gemini_api_key)

    def supports_structured_output(self) -> bool:
        # Gemini sí soporta salida estructurada nativa (response_schema
        # en GenerateContentConfig), pero no está implementado todavía
        # aquí — se deja en False para que LLMClient use su loop de
        # reparación mientras tanto. Ver TODO en generate().
        return False

    def supports_streaming(self) -> bool:
        return True

    async def generate_stream(
        self, request: LLMRequest, collector: StreamUsageCollector | None = None
    ):
        """Yields text deltas as they arrive from Gemini. No retries,
        no structured-output repair -- see LLMClient.generate_stream()
        for why those don't mix well with streaming.

        If a collector is passed, it gets filled with the aggregated
        LLMResponse (full text, real token usage, finish_reason) once
        the stream ends -- built from the accumulated chunks, not
        from any single one, since usage_metadata typically only
        arrives fully populated on the final chunk."""
        started_at = time.perf_counter()
        system_instruction, contents = self._build_contents(request)

        config = genai_types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=request.temperature,
            max_output_tokens=request.max_tokens,
        )

        stream = await self._client.aio.models.generate_content_stream(
            model=request.model,
            contents=contents,
            config=config,
        )

        accumulated_text: list[str] = []
        last_chunk = None

        async for chunk in stream:
            last_chunk = chunk
            # Not every chunk carries text (e.g. the final chunk may
            # only carry usage_metadata with no new content).
            if chunk.text:
                accumulated_text.append(chunk.text)
                yield chunk.text

        if collector is not None:
            collector.final_response = self._build_stream_final_response(
                last_chunk, "".join(accumulated_text), request.model, started_at
            )

    def _build_stream_final_response(
        self, last_chunk, full_text: str, model: str, started_at: float
    ) -> LLMResponse | None:
        """Best-effort: if the last chunk doesn't carry the usage/
        candidate info we expect, returns None instead of raising --
        a stream that already succeeded for the caller shouldn't
        break just because cost accounting couldn't be completed."""
        if last_chunk is None:
            return None

        try:
            candidate = last_chunk.candidates[0] if last_chunk.candidates else None
            mapped_finish_reason, _ = self._extract_finish_reason(
                candidate.finish_reason if candidate else None
            )
            usage = last_chunk.usage_metadata
            return LLMResponse(
                content=full_text,
                model=model,
                provider=self.name,
                tokens_used=TokenUsage(
                    input_tokens=usage.prompt_token_count or 0,
                    output_tokens=(usage.candidates_token_count or 0)
                    + (getattr(usage, "thoughts_token_count", None) or 0),
                ),
                latency_ms=(time.perf_counter() - started_at) * 1000,
                finish_reason=mapped_finish_reason,
            )
        except (AttributeError, IndexError):
            return None

    async def generate(self, request: LLMRequest) -> LLMResponse:
        started_at = time.perf_counter()

        system_instruction, contents = self._build_contents(request)

        config = genai_types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=request.temperature,
            max_output_tokens=request.max_tokens,
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=request.model,
                contents=contents,
                config=config,
            )
        except genai_errors.ClientError as exc:
            if exc.code == 429:
                raise RateLimitError(
                    str(exc), provider=self.name, original_error=exc
                ) from exc
            if exc.code == 400 and self._looks_like_content_filter(exc):
                raise ContentFilterError(
                    str(exc), provider=self.name, original_error=exc
                ) from exc
            raise ProviderError(
                str(exc), provider=self.name, original_error=exc
            ) from exc
        except genai_errors.ServerError as exc:
            raise ProviderError(
                str(exc), provider=self.name, original_error=exc
            ) from exc
        except TimeoutError as exc:
            # httpx.TimeoutException hereda de TimeoutError estándar de
            # Python; el SDK lo propaga tal cual sin envolverlo.
            raise LLMTimeoutError(
                str(exc), provider=self.name, original_error=exc
            ) from exc

        latency_ms = (time.perf_counter() - started_at) * 1000

        return self._to_llm_response(response, request.model, latency_ms)

    @staticmethod
    def _build_contents(
        request: LLMRequest,
    ) -> tuple[str | None, list[dict]]:
        """Separa el mensaje system (va en config.system_instruction en
        Gemini, no en el array de contenidos) del resto de mensajes."""
        system_instruction: str | None = None
        contents: list[dict] = []

        for message in request.messages:
            if message.role == Role.SYSTEM:
                system_instruction = (
                    f"{system_instruction}\n{message.content}"
                    if system_instruction
                    else message.content
                )
                continue

            # Gemini usa "model" en vez de "assistant" para el rol del LLM.
            role = "model" if message.role == Role.ASSISTANT else "user"
            contents.append({"role": role, "parts": [{"text": message.content}]})

        return system_instruction, contents

    @staticmethod
    def _looks_like_content_filter(exc: genai_errors.ClientError) -> bool:
        details = str(exc.details).lower() if exc.details else ""
        return "safety" in details or "blocked" in details

    @staticmethod
    def _extract_finish_reason(raw_finish_reason) -> tuple[str, str]:
        """Returns (mapped, raw). candidate.finish_reason is a real
        Enum object (e.g. FinishReason.STOP), not a plain string --
        confirmed against a real API call. getattr(..., "name", ...)
        handles both the real enum (returns "STOP") and a plain
        string in tests (falls through to str(), which returns the
        string as-is)."""
        raw = (
            getattr(raw_finish_reason, "name", None) or str(raw_finish_reason)
            if raw_finish_reason
            else "STOP"
        )
        return _FINISH_REASON_MAP.get(raw, "stop"), raw

    def _to_llm_response(
        self,
        response: genai_types.GenerateContentResponse,
        model: str,
        latency_ms: float,
    ) -> LLMResponse:
        if not response.candidates:
            raise InvalidResponseError(
                "Gemini no devolvió ningún candidato (probable bloqueo "
                "de seguridad sin detalle)",
                provider=self.name,
            )

        candidate = response.candidates[0]
        mapped_finish_reason, finish_reason_raw = self._extract_finish_reason(
            candidate.finish_reason
        )

        if finish_reason_raw in _CONTENT_FILTER_REASONS:
            raise ContentFilterError(
                f"Gemini bloqueó la respuesta: {finish_reason_raw}",
                provider=self.name,
            )

        try:
            content = response.text
            usage = response.usage_metadata
        except (AttributeError, ValueError) as exc:
            raise InvalidResponseError(
                "No se pudo mapear la respuesta de Gemini a LLMResponse",
                provider=self.name,
                original_error=exc,
            ) from exc

        return LLMResponse(
            content=content or "",
            model=model,
            provider=self.name,
            tokens_used=TokenUsage(
                input_tokens=usage.prompt_token_count or 0,
                # Confirmed against a real API call: "thinking" models
                # (gemini-3.6-flash and later) spend tokens on internal
                # reasoning that are billed like output tokens but
                # reported separately as thoughts_token_count, not
                # folded into candidates_token_count. Omitting this
                # would silently undercount real cost.
                output_tokens=(usage.candidates_token_count or 0)
                + (getattr(usage, "thoughts_token_count", None) or 0),
            ),
            latency_ms=latency_ms,
            finish_reason=mapped_finish_reason,
        )