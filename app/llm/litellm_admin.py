"""Operaciones admin contra el LiteLLM Proxy.

Todo lo que autentica con settings.litellm_master_key vive acá, y en
ningún otro lado: el tráfico real de chat (app.llm.litellmprovider.litellm_provider)
solo acepta virtual keys, nunca la master key. Este módulo es la única
excepción reconocida a esa regla, porque generar/administrar virtual
keys es en sí mismo un llamado admin, no tráfico de chat.

Tres operaciones hoy, todas terminan en POST /key/generate:
  - generate_tenant_virtual_key(): una virtual key por organización,
    aprovisionada automáticamente (ver app.organizations.service).
  - create_emergency_fallback_key(): una virtual key única y acotada
    para cuando un tenant todavía no tiene la suya propia (ver
    app.organizations.dependencies.get_tenant_llm_client). Se corre a
    mano, una sola vez, vía scripts/create_emergency_fallback_key.py.
  - create_eval_virtual_key(): virtual key separada para
    eval_harness/, con acceso a los modelos barato Y caro (a
    diferencia de la de emergencia) para poder medir routing de
    verdad. Se corre a mano vía scripts/create_eval_virtual_key.py.
"""

from __future__ import annotations

from uuid import UUID

import httpx

from app.core.config import settings
from app.llm.errors import TenantVirtualKeyError

EMERGENCY_FALLBACK_KEY_ALIAS = "emergency-fallback"
EMERGENCY_FALLBACK_KEY_MODELS = ["gemini-3.6-flash"]

EVAL_KEY_ALIAS = "eval-harness"
# Solo los dos modelos que ModelRouter realmente usa (barato + caro) --
# no gpt-5.6-luna, que no participa del routing actual.
EVAL_KEY_MODELS = ["gemini-3.6-flash", "claude-sonnet-5"]


async def _generate_key(payload: dict) -> str:
    """POST /key/generate contra el proxy, autenticado con la master
    key. La key devuelta es un secreto operacional: esta función no la
    registra ni la incluye en mensajes de error."""
    if not settings.litellm_master_key:
        raise TenantVirtualKeyError(
            "LiteLLM no tiene una master key configurada", provider="litellm"
        )

    try:
        async with httpx.AsyncClient(
            base_url=settings.litellm_base_url.rstrip("/"), timeout=10.0
        ) as client:
            response = await client.post(
                "/key/generate",
                headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
                json=payload,
            )
            response.raise_for_status()
    except httpx.RequestError as exc:
        raise TenantVirtualKeyError(
            "No se pudo conectar al proxy LiteLLM para generar la virtual key",
            provider="litellm",
            original_error=exc,
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise TenantVirtualKeyError(
            "LiteLLM rechazó la generación de la virtual key",
            provider="litellm",
            original_error=exc,
        ) from exc

    try:
        key = response.json()["key"]
    except (KeyError, TypeError, ValueError) as exc:
        raise TenantVirtualKeyError(
            "LiteLLM devolvió una respuesta inválida al generar la virtual key",
            provider="litellm",
            original_error=exc,
        ) from exc

    if not isinstance(key, str) or not key:
        raise TenantVirtualKeyError(
            "LiteLLM devolvió una virtual key vacía", provider="litellm"
        )
    return key


async def generate_tenant_virtual_key(organization_id: UUID, max_budget: float) -> str:
    """Crea una virtual key de LiteLLM asociada al tenant indicado."""
    return await _generate_key(
        {
            "metadata": {"tenant_id": str(organization_id)},
            "max_budget": max_budget,
        }
    )


async def create_emergency_fallback_key() -> str:
    """Crea la virtual key de emergencia usada por get_tenant_llm_client()
    cuando una organización todavía no tiene la suya propia.

    Acotada a propósito: budget diario bajo (configurable vía
    settings.emergency_fallback_daily_budget_usd) y un único modelo
    barato -- ver scripts/create_emergency_fallback_key.py, el punto
    de entrada real."""
    return await _generate_key(
        {
            "key_alias": EMERGENCY_FALLBACK_KEY_ALIAS,
            "max_budget": settings.emergency_fallback_daily_budget_usd,
            "budget_duration": "1d",
            "models": EMERGENCY_FALLBACK_KEY_MODELS,
        }
    )


async def create_eval_virtual_key() -> str:
    """Crea la virtual key usada por eval_harness/model_routing_eval.py.

    Separada de la de emergencia a propósito: esa está restringida a
    gemini-3.6-flash y bloquearía cualquier caso del dataset que el
    router decida enviar al modelo caro, arruinando la medición de
    accuracy en vez de solo el costo."""
    return await _generate_key(
        {
            "key_alias": EVAL_KEY_ALIAS,
            "max_budget": settings.eval_virtual_key_daily_budget_usd,
            "budget_duration": "1d",
            "models": EVAL_KEY_MODELS,
        }
    )
