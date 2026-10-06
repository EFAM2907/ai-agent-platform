"""Cliente MCP del agente de chat hacia `mcp_server/` -- reemplaza las
tools in-process que antes construian
`app.users.tools.build_user_management_tools` /
`app.shipments.tools.build_shipment_tools` (ver docs/roadmap.md seccion
2, "Pendiente real que queda"). El chat ya no importa `UserService`/
`ShipmentService` ni reaplica sus reglas de permisos a mano: le pide la
lista de tools al servidor MCP y, para cada tool_call que el LLM pida,
la reenvia por el protocolo MCP estandar (streamable-http). El propio
`mcp_server/server.py` es quien reenvia a su vez ese request HTTP a la
API REST (`app.users.api`, `app.shipments.api`), con el mismo Bearer
token del usuario que abrio esta conversacion -- la autorizacion sigue
viviendo en un solo lugar (la API REST), no duplicada aca.

Una consecuencia real de este cambio: `mcp_server/` pasa de ser "un
camino mas, opcional, para otros clientes MCP" a ser una dependencia
dura del chat -- si no esta corriendo, ninguna conversacion con tools
funciona (ni siquiera una que termine sin llamar ninguna, porque la
lista de tools se pide al abrir la sesion, antes de saber si el LLM
las va a usar). Ver MCP_SERVER_URL en app.core.config.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import mcp_types
from fastmcp.client import Client, StreamableHttpTransport

from app.core.config import settings
from app.llm.schemas import ToolDefinition
from app.llm.tools import Tool

logger = logging.getLogger(__name__)


@asynccontextmanager
async def open_mcp_tools(bearer_token: str) -> AsyncIterator[list[Tool]]:
    """Abre una sesion MCP (streamable-http) contra `settings.mcp_server_url`,
    reenviando `bearer_token` como el mismo header Authorization que ya
    autentico este request de chat (ver
    app.core.dependencies.get_bearer_token), y devuelve la lista de
    Tools que `run_tool_loop`/`run_streaming_tool_loop` pueden ofrecerle
    al LLM.

    La sesion queda abierta mientras el `async with` este activo a
    proposito: un turno con varias vueltas de tool_calls necesita poder
    seguir llamando al mismo servidor MCP entre cada una, no solo una
    vez al principio -- por eso esto es un context manager y no una
    funcion que devuelve la lista y listo. El caller (ChatService) debe
    envolver TODO el tool loop dentro del `async with`, no solo armar
    el LLMRequest."""
    transport = StreamableHttpTransport(
        url=settings.mcp_server_url,
        headers={"Authorization": f"Bearer {bearer_token}"},
    )
    async with Client(transport) as client:
        mcp_tools = await client.list_tools()
        yield [_build_tool(client, mcp_tool) for mcp_tool in mcp_tools]


def _build_tool(client: Client, mcp_tool: mcp_types.Tool) -> Tool:
    """Traduce un `mcp_types.Tool` (lo que el servidor MCP anuncia) a un
    `app.llm.tools.Tool` (lo que el loop generico necesita): mismo
    nombre/descripcion/JSON schema que ya definio `mcp_server/server.py`
    via sus docstrings y type hints -- no se redeclaran a mano en este
    lado como antes, asi que no hay dos copias del schema de cada tool
    para que se desincronicen entre si."""

    async def handler(**kwargs: Any) -> Any:
        result = await client.call_tool(mcp_tool.name, kwargs)
        if result.data is not None:
            return result.data
        # Sin contenido estructurado (una tool que solo devolviera
        # texto plano, no es el caso de ninguna de las de hoy pero no
        # hay que asumirlo): el primer bloque de texto es lo mas
        # cercano a "la respuesta" que hay.
        for block in result.content:
            if isinstance(block, mcp_types.TextContent):
                return block.text
        return None

    return Tool(
        definition=ToolDefinition(
            name=mcp_tool.name,
            description=mcp_tool.description or "",
            # `input_schema`, no `inputSchema` -- ese es solo el alias
            # de (de)serializacion del modelo pydantic de mcp_types,
            # no un atributo real del objeto (AttributeError si se usa
            # aca).
            parameters=mcp_tool.input_schema,
        ),
        handler=handler,
    )
