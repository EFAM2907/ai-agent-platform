"""
Punto único donde se decide cuál es el LLMClient "por defecto" de la
app. Vive separado de client.py a propósito: LLMClient no debe
conocer OpenAIProvider (ni ningún provider concreto), así que la
conveniencia de "dame el default" vive acá, no en el constructor.
"""

from __future__ import annotations

from app.llm.client import LLMClient
from app.llm.fallback_client import FallbackLLMClient
from app.llm.providers.anthropic_provider import AnthropicProvider
from app.llm.providers.gemini_provider import GeminiProvider
from app.llm.providers.openai_provider import OpenAIProvider


def get_default_llm_client() -> LLMClient:
    """Factory usada por dependency injection (ej. FastAPI Depends)
    en el resto de la app. Si mañana el default cambia a Anthropic,
    este es el único lugar que se toca."""
    return LLMClient(OpenAIProvider())


def get_fallback_llm_client() -> FallbackLLMClient:
    """Factory que arma la cadena de fallback completa: OpenAI primero
    (más rápido/barato en general), Anthropic y Gemini como respaldo
    si el anterior falla o tiene el circuito abierto. Cambiar el
    orden de prioridad se hace acá, en un solo lugar."""
    return FallbackLLMClient(
        [
            LLMClient(OpenAIProvider()),
            LLMClient(AnthropicProvider()),
            LLMClient(GeminiProvider()),
        ]
    )