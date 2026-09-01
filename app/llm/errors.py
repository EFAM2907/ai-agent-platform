"""
Taxonomía de errores propia de la capa LLM.

Ningún código fuera de app/llm/ debe atrapar excepciones nativas de
OpenAI, Anthropic o Gemini. Cada provider traduce sus propios errores
a esta jerarquía, para que el resto de la app (RAG, agentes,
orquestador) maneje fallos sin conocer qué proveedor está detrás.
"""

from __future__ import annotations


class LLMError(Exception):
    """Excepción base para cualquier fallo dentro de la capa LLM.

    Guarda el proveedor y el error original (si existe) para
    trazabilidad en logs/Langfuse, sin exponer el tipo nativo al
    resto de la app.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        original_error: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.original_error = original_error

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(provider={self.provider!r}, "
            f"message={str(self)!r})"
        )


class RateLimitError(LLMError):
    """El proveedor respondió con un límite de tasa excedido (429)."""

    def __init__(
        self,
        message: str = "Rate limit excedido",
        *,
        provider: str | None = None,
        original_error: Exception | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, provider=provider, original_error=original_error)
        self.retry_after = retry_after


class TimeoutError_(LLMError):
    """La solicitud excedió el timeout configurado (connect, read o total).

    Nombrada con guion bajo final para no chocar con el TimeoutError
    nativo de Python; se re-exporta como TimeoutError en __init__.py
    del paquete llm si se prefiere ese nombre en el resto de la app.
    """


class ProviderError(LLMError):
    """Fallo genérico del lado del proveedor (5xx, respuesta inválida
    a nivel de transporte, etc.) que no encaja en una categoría más
    específica."""


class ContentFilterError(LLMError):
    """El proveedor bloqueó la solicitud o la respuesta por políticas
    de contenido."""


class InvalidResponseError(LLMError):
    """La respuesta del proveedor no pudo mapearse al schema esperado
    (ej. salida estructurada que no cumple el Pydantic model, o
    function calling con argumentos no parseables)."""


class AllProvidersFailedError(LLMError):
    """Se agotó la cadena de fallback: todos los proveedores/modelos
    configurados fallaron para esta solicitud."""

    def __init__(
        self,
        message: str = "Todos los proveedores fallaron",
        *,
        attempts: list[LLMError] | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts or []