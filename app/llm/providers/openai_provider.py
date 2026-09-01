"""
Implementación concreta de LLMProvider para OpenAI.

Traduce entre el formato normalizado (LLMRequest/LLMResponse) y el
SDK oficial de OpenAI. Ningún tipo de `openai` debe escapar de este
archivo hacia el resto de la app.
"""

from __future__ import annotations

import time

import openai
from openai import AsyncOpenAI

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

# Mapea el finish_reason nativo de OpenAI al set fijo de LLMResponse.
_FINISH_REASON_MAP: dict[str, str] = {
    "stop": "stop",
    "length": "length",
    "content_filter": "content_filter",
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
}


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self) -> None:
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    def supports_structured_output(self) -> bool:
        return True

    async def generate(self, request: LLMRequest) -> LLMResponse:
        started_at = time.perf_counter()

        try:
            response = await self.client.responses.create(
                model=request.model,
                input=[
                    {"role": message.role.value, "content": message.content}
                    for message in request.messages
                ],
                temperature=request.temperature,
                max_output_tokens=request.max_tokens,
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
            # OpenAI usa este código también para bloqueos de contenido.
            if "content_filter" in str(exc).lower():
                raise ContentFilterError(
                    str(exc), provider=self.name, original_error=exc
                ) from exc
            raise ProviderError(
                str(exc), provider=self.name, original_error=exc
            ) from exc
        except openai.APIError as exc:
            raise ProviderError(
                str(exc), provider=self.name, original_error=exc
            ) from exc

        latency_ms = (time.perf_counter() - started_at) * 1000

        try:
            content = response.output_text
            usage = response.usage
            finish_reason_raw = response.output[0].finish_reason if response.output else "stop"
        except (AttributeError, IndexError) as exc:
            raise InvalidResponseError(
                "No se pudo mapear la respuesta de OpenAI a LLMResponse",
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