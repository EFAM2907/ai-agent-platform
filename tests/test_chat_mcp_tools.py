"""Tests de app.chat.mcp_tools -- el cliente MCP que ChatService abre
por turno contra mcp_server/ (ver docs/roadmap.md seccion 2). No habla
con un servidor MCP real: mockea fastmcp.client.Client (y
StreamableHttpTransport) para verificar dos cosas por separado, mismo
criterio que ya usan tests/test_mcp_server.py del otro lado de la
conexion:

1. open_mcp_tools abre la sesion contra la URL/headers correctos y
   traduce cada mcp_types.Tool anunciado a un app.llm.tools.Tool.
2. El handler de cada Tool reenvia la tool call al Client y devuelve
   algo que app.llm.tools._execute_tool pueda serializar a JSON.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import mcp_types
import pytest
from fastmcp.client.client import CallToolResult

from app.chat.mcp_tools import open_mcp_tools
from app.core.config import settings


def _mcp_tool(name: str = "list_users") -> mcp_types.Tool:
    return mcp_types.Tool(
        name=name,
        description=f"Descripcion de {name}",
        inputSchema={"type": "object", "properties": {"limit": {"type": "integer"}}},
    )


def _fake_client(list_tools_result, call_tool_result=None):
    """Un fastmcp.client.Client falso que funciona como `async with` --
    list_tools() devuelve `list_tools_result`, call_tool() devuelve
    `call_tool_result` (si se paso)."""
    client = MagicMock()
    client.list_tools = AsyncMock(return_value=list_tools_result)
    if call_tool_result is not None:
        client.call_tool = AsyncMock(return_value=call_tool_result)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


class TestOpenMcpTools:
    @pytest.mark.asyncio
    async def test_abre_la_sesion_con_la_url_y_el_bearer_token(self):
        client = _fake_client([_mcp_tool("list_users")])
        fake_transport = MagicMock()

        with (
            patch("app.chat.mcp_tools.Client", return_value=client) as client_cls,
            patch(
                "app.chat.mcp_tools.StreamableHttpTransport", return_value=fake_transport
            ) as transport_cls,
        ):
            async with open_mcp_tools("el-token-del-usuario") as tools:
                assert [t.name for t in tools] == ["list_users"]

        transport_cls.assert_called_once_with(
            url=settings.mcp_server_url,
            headers={"Authorization": "Bearer el-token-del-usuario"},
        )
        client_cls.assert_called_once_with(fake_transport)
        client.__aenter__.assert_awaited_once()
        client.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_traduce_nombre_descripcion_y_schema_de_cada_mcp_tool(self):
        mcp_tool = _mcp_tool("get_shipment_status")
        client = _fake_client([mcp_tool])

        with (
            patch("app.chat.mcp_tools.Client", return_value=client),
            patch("app.chat.mcp_tools.StreamableHttpTransport"),
        ):
            async with open_mcp_tools("t") as tools:
                [tool] = tools

        assert tool.name == "get_shipment_status"
        assert tool.definition.description == "Descripcion de get_shipment_status"
        assert tool.definition.parameters == {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
        }

    @pytest.mark.asyncio
    async def test_el_handler_reenvia_la_tool_call_y_devuelve_structured_data(self):
        mcp_tool = _mcp_tool("list_shipments")
        result = CallToolResult(
            content=[mcp_types.TextContent(type="text", text="no se usa")],
            structured_content={"total_shipments": 3},
            meta=None,
            data={"total_shipments": 3},
        )
        client = _fake_client([mcp_tool], call_tool_result=result)

        with (
            patch("app.chat.mcp_tools.Client", return_value=client),
            patch("app.chat.mcp_tools.StreamableHttpTransport"),
        ):
            async with open_mcp_tools("t") as tools:
                [tool] = tools
                returned = await tool.handler(limit=5)

        client.call_tool.assert_awaited_once_with("list_shipments", {"limit": 5})
        assert returned == {"total_shipments": 3}

    @pytest.mark.asyncio
    async def test_el_handler_cae_al_texto_si_no_hay_structured_data(self):
        mcp_tool = _mcp_tool("get_shipment_status")
        result = CallToolResult(
            content=[mcp_types.TextContent(type="text", text="solo texto plano")],
            structured_content=None,
            meta=None,
            data=None,
        )
        client = _fake_client([mcp_tool], call_tool_result=result)

        with (
            patch("app.chat.mcp_tools.Client", return_value=client),
            patch("app.chat.mcp_tools.StreamableHttpTransport"),
        ):
            async with open_mcp_tools("t") as tools:
                [tool] = tools
                returned = await tool.handler(tracking_number="ABC123")

        assert returned == "solo texto plano"
