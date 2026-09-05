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
        # candidate.finish_reason is a real Enum object (e.g.
        # FinishReason.STOP), not a plain string -- confirmed against
        # a real API call. getattr(..., "name", ...) handles both the
        # real enum (returns "STOP") and a plain string in tests
        # (falls through to str(), which returns the string as-is).
        raw_finish_reason = candidate.finish_reason
        finish_reason_raw = (
            getattr(raw_finish_reason, "name", None) or str(raw_finish_reason)
            if raw_finish_reason
            else "STOP"
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
            finish_reason=_FINISH_REASON_MAP.get(finish_reason_raw, "stop"),
        )