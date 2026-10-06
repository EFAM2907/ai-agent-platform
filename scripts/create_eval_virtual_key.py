"""Punto de entrada CLI para crear la virtual key de eval_harness/.

La lógica real vive en app.llm.litellm_admin.create_eval_virtual_key --
este script solo la invoca y muestra el resultado.

Uso (con el proxy LiteLLM ya arriba, ver docker-compose.yml):
    python -m scripts.create_eval_virtual_key

Imprime la key generada -- copiarla a mano en .env como
LITELLM_EVAL_VIRTUAL_KEY. No se guarda en ningún lado por este script.
"""

from __future__ import annotations

import asyncio

from app.core.config import settings
from app.llm.litellm_admin import EVAL_KEY_ALIAS, EVAL_KEY_MODELS, create_eval_virtual_key


async def main() -> None:
    key = await create_eval_virtual_key()
    print(
        "Virtual key de eval creada "
        f"(alias={EVAL_KEY_ALIAS!r}, "
        f"budget=${settings.eval_virtual_key_daily_budget_usd}/dia, "
        f"models={EVAL_KEY_MODELS}):\n\n"
        f"{key}\n\n"
        "Copiala a .env como LITELLM_EVAL_VIRTUAL_KEY -- no queda "
        "guardada en ningun otro lado."
    )


if __name__ == "__main__":
    asyncio.run(main())
