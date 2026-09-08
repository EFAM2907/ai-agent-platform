"""
Contrato que todo provider de LLM debe cumplir.

RAG, agentes y orquestador dependen únicamente de esta interfaz
(vía LLMClient), nunca de una implementación concreta como
OpenAIProvider. Esto es lo que permite agregar Anthropic o Gemini
sin tocar una sola línea fuera de app/llm/providers/.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.llm.schemas import LLMRequest, LLMResponse
from app.llm.streaming import StreamUsageCollector


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

    def supports_streaming(self) -> bool:
        """False por default. Se agregó DESPUÉS de que los tres
        providers concretos ya existían -- por eso NO es
        @abstractmethod: forzarlo hubiera roto OpenAIProvider y
        AnthropicProvider hasta que también implementaran streaming.
        Cada provider que sí soporte streaming debe sobreescribir
        esto junto con generate_stream()."""
        return False

    async def generate_stream(
        self, request: LLMRequest, collector: StreamUsageCollector | None = None
    ) -> AsyncIterator[str]:
        """Genera la respuesta en chunks de texto, para mostrarla en
        vivo (SSE) en vez de esperar la respuesta completa. Sin
        retries ni loop de reparación de salida estructurada -- ver
        LLMClient.generate_stream() para el porqué.

        If a collector is passed, providers that support streaming
        should fill collector.final_response once the stream ends,
        so the caller (LLMClient) can log cost and close the trace
        with real usage data -- without changing what gets yielded.

        Default: no soportado. Los providers que sí lo implementen
        sobreescriben este método."""
        raise NotImplementedError(
            f"El provider '{self.name}' todavía no soporta streaming"
        )
        yield  # pragma: no cover -- hace de este un generador válido