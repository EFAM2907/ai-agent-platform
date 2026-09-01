"""
Modelos normalizados de la capa LLM.

Ningún campo aquí debe ser específico de un proveedor (OpenAI,
Anthropic, Gemini). Cada provider traduce su formato nativo hacia y
desde estos modelos, para que el resto de la app (RAG, agentes,
orquestador) trabaje siempre con el mismo contrato.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Message(BaseModel):
    role: Role
    content: str


class LLMRequest(BaseModel):
    """Entrada normalizada hacia cualquier provider."""

    messages: list[Message]
    model: str
    temperature: float = 0.0
    max_tokens: int | None = None

    # Salida estructurada: si se pasa, el provider debe forzar que la
    # respuesta cumpla este JSON schema (via function calling, JSON
    # mode, o el mecanismo nativo que tenga cada API).
    response_schema: dict[str, Any] | None = None

    # Metadata para trazabilidad (Langfuse, logging de costo), no se
    # envía al proveedor.
    tenant_id: str | None = None
    request_tag: str | None = None


class TokenUsage(BaseModel):
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMResponse(BaseModel):
    """Salida normalizada de cualquier provider."""

    content: str
    model: str
    provider: str
    tokens_used: TokenUsage
    latency_ms: float
    finish_reason: Literal["stop", "length", "content_filter", "tool_calls"]

    # Costo estimado en USD, calculado por el provider según su
    # tabla de precios vigente. Puede quedar en None si el provider
    # no tiene precio configurado todavía.
    estimated_cost_usd: float | None = None

    # Presente solo si la request pidió response_schema y el
    # provider logró parsear la salida contra ese schema.
    parsed: dict[str, Any] | None = None