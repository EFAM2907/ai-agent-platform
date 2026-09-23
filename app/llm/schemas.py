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


class ToolCall(BaseModel):
    """Una llamada a herramienta concreta que el LLM pidió -- el `id`
    es lo que permite casar el resultado con la llamada correcta
    cuando el LLM pide varias tools en el mismo turno."""

    id: str
    name: str
    arguments: dict[str, Any]


class Message(BaseModel):
    role: Role
    # Vacío ("") en un mensaje ASSISTANT que solo trae tool_calls -- es
    # normal en la API de OpenAI-compatible que el modelo no devuelva
    # texto visible cuando lo que está haciendo es pedir una tool.
    content: str = ""

    # Solo presente en un mensaje ASSISTANT que el LLM devolvió con
    # finish_reason="tool_calls".
    tool_calls: list[ToolCall] | None = None
    # Solo presente en un mensaje TOOL: a cuál ToolCall.id responde
    # este resultado. El proveedor lo necesita para casar la respuesta
    # con la llamada correcta.
    tool_call_id: str | None = None


class ToolDefinition(BaseModel):
    """Lo que el LLM necesita saber de una tool para poder decidir
    llamarla: nombre, para qué sirve, y el JSON schema de sus
    argumentos. Deliberadamente NO incluye la función que la ejecuta
    -- eso es responsabilidad de quien ejecuta el loop (ver
    app.llm.tools.Tool), no de este contrato normalizado con el
    proveedor."""

    name: str
    description: str
    parameters: dict[str, Any]


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

    # Tools disponibles para que el LLM decida llamar. None (default)
    # es "sin tools" -- el comportamiento de siempre para cualquier
    # llamada que no las use (RAG, ModelRouter, etc.). Tambien
    # soportado en generate_stream() (ver LiteLLMProvider.generate_stream):
    # los argumentos de tool_calls llegan fragmentados en streaming, y
    # el provider los reensambla por indice antes de exponerlos en
    # LLMResponse.tool_calls via el StreamUsageCollector -- ver
    # app.llm.tools.run_streaming_tool_loop para el loop que los
    # consume.
    tools: list[ToolDefinition] | None = None

    # Parámetro unificado entre proveedores para controlar "thinking"/
    # razonamiento extendido (ver docs.litellm.ai/docs/reasoning_content):
    # "low"/"medium"/"high" ajustan la profundidad, "none" lo apaga por
    # completo. None (default) significa "no tocar este parámetro" -- se
    # omite de la request al proveedor, el mismo comportamiento de
    # siempre para cualquier llamada que no lo setee explícitamente (RAG
    # embeddings, el clasificador de ModelRouter, etc.). Solo tiene
    # efecto real en modelos "thinking" (ej. gemini-3.6-flash); en un
    # modelo que no razona, el gateway simplemente lo ignora.
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None

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

    # Presente solo si finish_reason == "tool_calls" -- las llamadas a
    # herramienta que el LLM pidió, ya parseadas (arguments como dict,
    # no como el string JSON crudo que manda el proveedor).
    tool_calls: list[ToolCall] | None = None