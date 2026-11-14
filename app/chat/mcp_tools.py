"""Cliente MCP del agente de chat hacia `mcp_server/` -- reemplaza las
tools in-process que antes construian
`app.users.tools.build_user_management_tools` /
`app.shipments.tools.build_shipment_tools` (ver docs/roadmap.md seccion
2, "Pendiente real que queda"). El chat ya no importa `UserService`/
`ShipmentService` ni reaplica sus reglas de permisos a mano: le pide la
lista de tools al servidor MCP y, para cada tool_call que el LLM pida,
la reenvia por el protocolo MCP estandar (streamable-http).

El JWT del usuario NO viaja al MCP server: se intercambia en el STS
(app.core.sts, RFC 8693) por un token delegado de vida corta, con
aud=mcp-server, scope reducido a lo que el rol permite y claim `act`
(actor = chat-service, sujeto = el usuario). Las tools destructivas
(app.core.scopes.CONFIRMATION_REQUIRED_TOOLS) nunca se ejecutan desde el
loop del LLM: se convierten en una accion pendiente de aprobacion
humana (ver ChatService.approve_action).

Una consecuencia real de este cambio: `mcp_server/` pasa de ser "un
camino mas, opcional, para otros clientes MCP" a ser una dependencia
dura del chat -- si no esta corriendo, ninguna conversacion con tools
funciona (ni siquiera una que termine sin llamar ninguna, porque la
lista de tools se pide al abrir la sesion, antes de saber si el LLM
las va a usar). Ver MCP_SERVER_URL en app.core.config.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import jwt
import mcp_types
from fastmcp.client import Client, StreamableHttpTransport

from app.core.config import settings
from app.core.scopes import CONFIRMATION_REQUIRED_TOOLS, TOOL_SCOPE, scopes_for_role
from app.core.sts import args_hash, issue_delegated_token
from app.llm.schemas import ToolDefinition
from app.llm.tools import Tool
from app.users.models import User

logger = logging.getLogger(__name__)

# Recibe (nombre de la tool, argumentos) cuando el LLM pide una tool
# destructiva y devuelve lo que el LLM vera como resultado (p. ej.
# "pendiente de aprobacion"). Ver ChatService._request_confirmation.
ConfirmationHandler = Callable[[str, dict[str, Any]], Awaitable[Any]]

# Delegacion del turno (claims del token, sin la firma): para la traza.
DelegationObserver = Callable[[dict[str, Any]], None]


def _transport(token: str) -> StreamableHttpTransport:
    return StreamableHttpTransport(
        url=settings.mcp_server_url,
        headers={"Authorization": f"Bearer {token}"},
    )


@asynccontextmanager
async def open_mcp_tools(
    user: User,
    *,
    confirmation_handler: ConfirmationHandler | None = None,
    on_delegation: DelegationObserver | None = None,
) -> AsyncIterator[list[Tool]]:
    """Abre una sesion MCP (streamable-http) contra `settings.mcp_server_url`
    con un token DELEGADO para `user` (token exchange, no el JWT del
    usuario) y devuelve la lista de Tools que `run_tool_loop`/
    `run_streaming_tool_loop` pueden ofrecerle al LLM.

    La sesion queda abierta mientras el `async with` este activo a
    proposito: un turno con varias vueltas de tool_calls necesita poder
    seguir llamando al mismo servidor MCP entre cada una -- por eso esto
    es un context manager. El caller (ChatService) debe envolver TODO el
    tool loop dentro del `async with`.

    Sin `confirmation_handler`, las tools destructivas se rechazan
    (falla hacia "no ejecutar")."""
    token = issue_delegated_token(user, scopes_for_role(user.role))
    if on_delegation is not None:
        on_delegation(jwt.decode(token, options={"verify_signature": False}))
    async with Client(_transport(token)) as client:
        mcp_tools = await client.list_tools()
        yield [_build_tool(client, mcp_tool, confirmation_handler) for mcp_tool in mcp_tools]


async def call_approved_tool(
    user: User, tool_name: str, arguments: dict[str, Any], action_id: str
) -> Any:
    """Ejecuta UNA tool destructiva ya aprobada por el humano: token
    delegado con solo el scope de esa tool, vida de segundos y claims
    `approved_action`/`args_hash` que atan la aprobacion a estos
    argumentos exactos (el MCP server lo verifica)."""
    scope = TOOL_SCOPE[tool_name]
    token = issue_delegated_token(
        user,
        {scope},
        approved_action=action_id,
        approved_args_hash=args_hash(arguments),
        ttl_seconds=settings.approved_action_ttl_seconds,
    )
    async with Client(_transport(token)) as client:
        return _unwrap(await client.call_tool(tool_name, arguments))


def _unwrap(result: Any) -> Any:
    if result.data is not None:
        return result.data
    # Sin contenido estructurado (una tool que solo devolviera texto
    # plano): el primer bloque de texto es lo mas cercano a "la respuesta".
    for block in result.content:
        if isinstance(block, mcp_types.TextContent):
            return block.text
    return None


def _build_tool(
    client: Client,
    mcp_tool: mcp_types.Tool,
    confirmation_handler: ConfirmationHandler | None = None,
) -> Tool:
    """Traduce un `mcp_types.Tool` (lo que el servidor MCP anuncia) a un
    `app.llm.tools.Tool` (lo que el loop generico necesita): mismo
    nombre/descripcion/JSON schema que ya definio `mcp_server/server.py`
    via sus docstrings y type hints."""

    needs_confirmation = mcp_tool.name in CONFIRMATION_REQUIRED_TOOLS

    async def handler(**kwargs: Any) -> Any:
        if needs_confirmation:
            if confirmation_handler is None:
                return {
                    "error": "Esta accion requiere la aprobacion explicita del usuario "
                    "y no esta disponible en este contexto.",
                }
            return await confirmation_handler(mcp_tool.name, kwargs)
        return _unwrap(await client.call_tool(mcp_tool.name, kwargs))

    return Tool(
        definition=ToolDefinition(
            name=mcp_tool.name,
            description=mcp_tool.description or "",
            # `input_schema`, no `inputSchema` -- ese es solo el alias
            # de (de)serializacion del modelo pydantic de mcp_types.
            parameters=mcp_tool.input_schema,
        ),
        handler=handler,
    )
