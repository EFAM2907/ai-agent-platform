"""
Punto único donde se decide cuál es el LLMClient "por defecto" de la
app. Vive separado de client.py a propósito: LLMClient no debe
conocer OpenAIProvider (ni ningún provider concreto), así que la
conveniencia de "dame el default" vive acá, no en el constructor.
"""

from __future__ import annotations

from app.core.config import settings
from app.llm.client import LLMClient
from app.llm.cost_logger import CostLogger
from app.llm.fallback_client import FallbackLLMClient
from app.llm.providers.anthropic_provider import AnthropicProvider
from app.llm.providers.gemini_provider import GeminiProvider
from app.llm.providers.openai_provider import OpenAIProvider
from app.llm.tracing import LangfuseTracer

# Instancia única compartida: todos los LLMClient armados por las
# factories de este módulo escriben al mismo archivo de log.
_default_cost_logger = CostLogger()


def _get_default_tracer() -> LangfuseTracer | None:
    """Only builds a tracer if Langfuse keys are actually configured
    -- this way the app keeps working before the Langfuse account is
    fully set up, instead of crashing on missing credentials."""
    if not getattr(settings, "langfuse_public_key", None):
        return None
    return LangfuseTracer()


_default_tracer = _get_default_tracer()


def get_default_llm_client() -> LLMClient:
    """Factory usada por dependency injection (ej. FastAPI Depends)
    en el resto de la app. Gemini es el default por ahora porque es
    el único proveedor con créditos disponibles para pruebas reales
    -- cuando OpenAI tenga créditos, este es el único lugar que se
    toca para volver a cambiarlo."""
    return LLMClient(
        GeminiProvider(), cost_logger=_default_cost_logger, tracer=_default_tracer
    )


def get_fallback_llm_client() -> FallbackLLMClient:
    """Factory que arma la cadena de fallback completa: Gemini
    primero (único proveedor con créditos disponibles ahora mismo),
    OpenAI y Anthropic como respaldo una vez tengan créditos. Cambiar
    el orden de prioridad se hace acá, en un solo lugar."""
    return FallbackLLMClient(
        [
            LLMClient(
                GeminiProvider(),
                cost_logger=_default_cost_logger,
                tracer=_default_tracer,
            ),
            LLMClient(
                OpenAIProvider(),
                cost_logger=_default_cost_logger,
                tracer=_default_tracer,
            ),
            LLMClient(
                AnthropicProvider(),
                cost_logger=_default_cost_logger,
                tracer=_default_tracer,
            ),
        ]
    )