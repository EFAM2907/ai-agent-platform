"""Punto de entrada CLI para crear la virtual key de emergencia.

La lógica real (POST /key/generate, budget acotado, restringida a
gemini-3.6-flash) vive en app.llm.litellm_admin.create_emergency_fallback_key
-- este script solo la invoca y muestra el resultado. Ver ese módulo
para el detalle de por qué existe esta key y cómo se usa
(app.organizations.dependencies.get_tenant_llm_client).

Uso (con el proxy LiteLLM ya arriba, ver docker-compose.yml):
    python -m scripts.create_emergency_fallback_key

Imprime la key generada -- copiarla a mano en .env como
LITELLM_EMERGENCY_FALLBACK_KEY. No se guarda en ningún lado por este
script: es un secreto operacional, igual que las virtual keys de
tenant (ver app.llm.litellm_admin.generate_tenant_virtual_key).
"""

from __future__ import annotations

import asyncio

from app.core.config import settings
from app.llm.litellm_admin import (
    EMERGENCY_FALLBACK_KEY_ALIAS,
    EMERGENCY_FALLBACK_KEY_MODELS,
    create_emergency_fallback_key,
)


async def main() -> None:
    key = await create_emergency_fallback_key()
    print(
        "Virtual key de emergencia creada "
        f"(alias={EMERGENCY_FALLBACK_KEY_ALIAS!r}, "
        f"budget=${settings.emergency_fallback_daily_budget_usd}/dia, "
        f"models={EMERGENCY_FALLBACK_KEY_MODELS}):\n\n"
        f"{key}\n\n"
        "Copiala a .env como LITELLM_EMERGENCY_FALLBACK_KEY -- no queda "
        "guardada en ningun otro lado."
    )


if __name__ == "__main__":
    asyncio.run(main())
