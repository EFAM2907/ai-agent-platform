"""Filtro de herramientas por rol (app.chat.tool_access) y su coherencia
con las herramientas que de verdad anuncia el servidor MCP."""

from __future__ import annotations

import asyncio

from app.chat.tool_access import TOOL_MIN_ROLE, filter_tools_for_role
from app.llm.schemas import ToolDefinition
from app.llm.tools import Tool
from app.users.models import UserRole
from mcp_server.server import mcp


def _tool(name: str) -> Tool:
    return Tool(
        definition=ToolDefinition(name=name, description=name, parameters={"type": "object"}),
        handler=lambda **_: None,
    )


def _all_tools() -> list[Tool]:
    return [_tool(name) for name in TOOL_MIN_ROLE]


def test_every_tool_the_mcp_server_announces_is_registered_with_a_minimum_role():
    announced = {tool.name for tool in asyncio.run(mcp.list_tools())}

    assert announced - set(TOOL_MIN_ROLE) == set(), "herramienta MCP sin rol minimo en TOOL_MIN_ROLE"
    assert set(TOOL_MIN_ROLE) - announced == set(), "TOOL_MIN_ROLE nombra una herramienta que no existe"


def test_roles_see_progressively_more_tools():
    counts = {
        role: len(filter_tools_for_role(_all_tools(), role))
        for role in (UserRole.VIEWER, UserRole.MEMBER, UserRole.ADMIN, UserRole.OWNER)
    }

    assert counts[UserRole.VIEWER] < counts[UserRole.MEMBER] < counts[UserRole.ADMIN]
    assert counts[UserRole.ADMIN] == counts[UserRole.OWNER] == len(TOOL_MIN_ROLE)


def test_a_viewer_never_gets_a_tool_that_writes_or_manages_accounts():
    visible = {t.name for t in filter_tools_for_role(_all_tools(), UserRole.VIEWER)}

    forbidden = {
        "create_user", "delete_user", "change_user_role", "update_user",
        "create_shipment", "update_shipment_status", "create_customer", "update_customer",
        "notify_shipment_customer", "open_claim", "update_claim_status",
        "poll_gmail_now", "sync_drive",
    }
    assert visible.isdisjoint(forbidden)


def test_account_management_and_integrations_are_admin_only():
    member = {t.name for t in filter_tools_for_role(_all_tools(), UserRole.MEMBER)}

    assert member.isdisjoint(
        {"create_user", "list_users", "gmail_status", "drive_status", "sync_drive", "list_kb_documents"}
    )
    assert {"create_shipment", "open_claim", "notify_shipment_customer"} <= member


def test_an_unregistered_tool_is_only_offered_to_admins():
    unknown = [_tool("herramienta_nueva")]

    assert filter_tools_for_role(unknown, UserRole.MEMBER) == []
    assert len(filter_tools_for_role(unknown, UserRole.ADMIN)) == 1


def test_accepts_the_role_as_a_plain_string():
    assert filter_tools_for_role([_tool("whoami")], "viewer")[0].name == "whoami"
