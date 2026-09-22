"""Verificacion manual (no un test automatizado) de que el loop de
tool-calling (app.llm.tools.run_tool_loop) funciona de punta a punta
contra el gateway LiteLLM real -- con una tool de juguete, antes de
tocar nada del dominio de Inter Rapidisimo.

La pregunta de prueba ("que hora es?") es deliberada: el LLM no tiene
reloj propio, asi que si la respuesta trae una hora real (no una
inventada) es la prueba de que de verdad delego en el backend en vez
de alucinar -- el mismo principio que las consultas de prueba del RAG,
aplicado ahora al tool-calling.

Uso (con Docker/LiteLLM arriba):
    python -m scripts.verify_tool_calling
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.core.config import settings
from app.llm.factory import get_default_llm_client
from app.llm.schemas import LLMRequest, Message, Role, ToolDefinition
from app.llm.tools import Tool, run_tool_loop


async def get_current_datetime() -> dict:
    """La tool de juguete: sin argumentos, devuelve la hora real del
    servidor -- el LLM no tiene forma de adivinar esto por su cuenta."""
    now = datetime.now(timezone.utc)
    return {"utc_datetime": now.isoformat(), "weekday": now.strftime("%A")}


async def main() -> None:
    # La key de emergencia alcanza aca: esta acotada a gemini-3.6-flash,
    # que es justo el modelo que este script usa -- no hace falta
    # buscar el organization_id de ningun tenant real solo para
    # verificar que el mecanismo funciona.
    llm_client = get_default_llm_client(
        tenant_virtual_key=settings.litellm_emergency_fallback_key
    )

    tool = Tool(
        definition=ToolDefinition(
            name="get_current_datetime",
            description=(
                "Devuelve la fecha y hora actual en UTC. Usala siempre "
                "que te pregunten que hora o que dia es -- no la "
                "adivines por tu cuenta."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        handler=get_current_datetime,
    )

    request = LLMRequest(
        messages=[
            Message(
                role=Role.SYSTEM,
                content=(
                    "Eres un asistente que responde preguntas usando las "
                    "tools disponibles cuando las necesitas."
                ),
            ),
            Message(role=Role.USER, content="Que hora es ahora mismo, en UTC?"),
        ],
        model="gemini-3.6-flash",
    )

    response = await run_tool_loop(llm_client, request, tools=[tool])

    print("Respuesta final del modelo:")
    print(response.content)
    print()
    print(f"Hora real del servidor en este momento: {datetime.now(timezone.utc).isoformat()}")
    print(
        "Si la respuesta del modelo trae una hora cercana a esa, el tool-calling "
        "funciono de punta a punta -- el modelo no tiene forma de saber la hora "
        "real sin haber llamado de verdad a get_current_datetime()."
    )


if __name__ == "__main__":
    asyncio.run(main())
