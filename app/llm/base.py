"""
Contrato que todo provider de LLM debe cumplir.

RAG, agentes y orquestador dependen únicamente de esta interfaz
(vía LLMClient), nunca de una implementación concreta como
OpenAIProvider. Esto es lo que permite agregar Anthropic o Gemini
sin tocar una sola línea fuera de app/llm/providers/.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.llm.schemas import LLMRequest, LLMResponse


class LLMProvider(ABC):
    """Interfaz abstracta para un proveedor de LLM.

    Cada implementación concreta (OpenAIProvider, AnthropicProvider,
    GeminiProvider) es responsable de:
      1. Traducir LLMRequest a su formato nativo de API.
      2. Ejecutar la llamada.
      3. Traducir la respuesta nativa (o el error nativo) a
         LLMResponse (o a una excepción de app.llm.errors).

    Ningún tipo nativo del SDK del proveedor debe cruzar esta
    frontera en ninguna dirección.
    """

    #: Nombre corto y estable del proveedor, usado en LLMResponse.provider
    #: y en logs/Langfuse. Ej: "openai", "anthropic", "gemini".
    name: str

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Ejecuta una generación contra el proveedor.

        Debe lanzar excepciones de app.llm.errors (RateLimitError,
        TimeoutError_, ProviderError, ContentFilterError,
        InvalidResponseError) en vez de dejar propagar la excepción
        nativa del SDK.
        """
        raise NotImplementedError

    @abstractmethod
    def supports_structured_output(self) -> bool:
        """Indica si este provider puede forzar una salida que cumpla
        request.response_schema (via function calling, JSON mode,
        etc.). LLMClient usa esto para decidir si aplica su propio
        loop de reparación cuando el provider no lo soporta nativo.
        """
        raise NotImplementedError