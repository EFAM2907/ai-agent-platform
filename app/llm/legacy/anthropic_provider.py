"""
Implementación concreta de LLMProvider para Anthropic.

Traduce entre el formato normalizado (LLMRequest/LLMResponse) y el
SDK oficial de Anthropic.
"""

from __future__ import annotations

import time
from typing import Any

import anthropic
from anthropic import AsyncAnthropic

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

# Mapea el stop_reason nativo de Anthropic al set fijo de LLMResponse.
_FINISH_REASON_MAP: dict[str, str] = {
    "end_turn": "stop",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
}


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self) -> None:
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    def supports_structured_output(self) -> bool:
        # SUPUESTO: Por ahora devolvemos False y dejamos que LLMClient use su loop de reparación,
        # a menos que implementemos tool-use forzado para structured output.
        return False

    async def generate(self, request: LLMRequest) -> LLMResponse:
        started_at = time.perf_counter()

        # Separar el mensaje de sistema del resto de mensajes
        system_prompt = ""
        anthropic_messages = []
        for msg in request.messages:
            if msg.role == Role.SYSTEM:
                system_prompt += msg.content + "\n"
            else:
                anthropic_messages.append({"role": msg.role.value, "content": msg.content})

        try:
            response = await self.client.messages.create(
                model=request.model,
                messages=anthropic_messages,
                system=system_prompt.strip() if system_prompt else None,
                temperature=request.temperature,
                max_tokens=request.max_tokens or 4096,  # Anthropic requiere max_tokens
            )
        except anthropic.RateLimitError as exc:
            # SUPUESTO: Anthropic usa el header 'retry-after' en segundos o una fecha.
            retry_after = getattr(exc.response, "headers", {}).get("retry-after")
            raise RateLimitError(
                str(exc),
                provider=self.name,
                original_error=exc,
                retry_after=float(retry_after) if retry_after and retry_after.isdigit() else None,
            ) from exc
        except anthropic.APITimeoutError as exc:
            raise LLMTimeoutError(
                str(exc), provider=self.name, original_error=exc
            ) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code == 400 and "content_filter" in str(exc).lower():
                raise ContentFilterError(
                    str(exc), provider=self.name, original_error=exc
                ) from exc
            raise ProviderError(
                str(exc), provider=self.name, original_error=exc
            ) from exc
        except anthropic.APIError as exc:
            raise ProviderError(
                str(exc), provider=self.name, original_error=exc
            ) from exc

        latency_ms = (time.perf_counter() - started_at) * 1000

        try:
            # En Anthropic, el contenido es una lista de ContentBlocks.
            # SUPUESTO: Tomamos el primer bloque de texto.
            content = ""
            for block in response.content:
                if block.type == "text":
                    content += block.text
            
            usage = response.usage
            # SUPUESTO: stop_reason es el campo correcto para finish_reason.
            finish_reason_raw = response.stop_reason
        except (AttributeError, IndexError) as exc:
            raise InvalidResponseError(
                "No se pudo mapear la respuesta de Anthropic a LLMResponse",
                provider=self.name,
                original_error=exc,
            ) from exc

        return LLMResponse(
            content=content,
            model=response.model,
            provider=self.name,
            tokens_used=TokenUsage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
            ),
            latency_ms=latency_ms,
            finish_reason=_FINISH_REASON_MAP.get(finish_reason_raw, "stop"),
        )
