"""Scopes OAuth de las tools del agente: que permiso exige cada tool del
servidor MCP y que rol minimo puede obtenerlo.

Fuente unica del lado de la API/ChatService:
- app.chat.tool_access filtra las tools visibles por scope.
- app.core.sts limita el `scope` del token delegado a la interseccion
  entre lo pedido y lo que el rol del usuario permite.
mcp_server/ declara el mismo mapa en cada `@tool(scope=...)` (es otro
contenedor y no importa `app`); tests/test_tool_access.py verifica que
ambos mapas coincidan.
"""

from __future__ import annotations

from app.users.models import ROLE_HIERARCHY, UserRole

# scope -> rol minimo que puede obtenerlo.
SCOPE_MIN_ROLE: dict[str, UserRole] = {
    "profile:read": UserRole.VIEWER,
    "users:read": UserRole.ADMIN,
    "users:write": UserRole.ADMIN,
    "users:delete": UserRole.ADMIN,
    "users:roles": UserRole.ADMIN,
    "shipments:read": UserRole.VIEWER,
    "shipments:write": UserRole.MEMBER,
    "shipments:notify": UserRole.MEMBER,
    "customers:read": UserRole.VIEWER,
    "customers:write": UserRole.MEMBER,
    "claims:read": UserRole.VIEWER,
    "claims:write": UserRole.MEMBER,
    "kb:read": UserRole.VIEWER,
    "kb:admin": UserRole.ADMIN,
    "gmail:read": UserRole.ADMIN,
    "gmail:poll": UserRole.ADMIN,
    "drive:read": UserRole.ADMIN,
    "drive:sync": UserRole.ADMIN,
}

# nombre de la tool MCP -> scope que exige.
TOOL_SCOPE: dict[str, str] = {
    "whoami": "profile:read",
    "create_user": "users:write",
    "list_users": "users:read",
    "get_user": "users:read",
    "update_user": "users:write",
    "delete_user": "users:delete",
    "change_user_role": "users:roles",
    "list_shipments": "shipments:read",
    "get_shipment_status": "shipments:read",
    "get_shipment_history": "shipments:read",
    "shipment_stats": "shipments:read",
    "list_delayed_shipments": "shipments:read",
    "list_customers": "customers:read",
    "get_customer": "customers:read",
    "create_customer": "customers:write",
    "update_customer": "customers:write",
    "create_shipment": "shipments:write",
    "update_shipment_status": "shipments:write",
    "notify_shipment_customer": "shipments:notify",
    "list_claims": "claims:read",
    "get_claim": "claims:read",
    "open_claim": "claims:write",
    "update_claim_status": "claims:write",
    "search_knowledge_base": "kb:read",
    "list_kb_documents": "kb:admin",
    "gmail_status": "gmail:read",
    "list_unresolved_emails": "gmail:read",
    "poll_gmail_now": "gmail:poll",
    "drive_status": "drive:read",
    "sync_drive": "drive:sync",
}

# Tools destructivas o irreversibles: el MCP server las rechaza aunque
# el scope este en el token, salvo que el token lleve la aprobacion
# humana de ESA llamada exacta (claims `approved_action`/`args_hash`).
CONFIRMATION_REQUIRED_TOOLS: frozenset[str] = frozenset(
    {
        "delete_user",
        "change_user_role",
        "notify_shipment_customer",
        "poll_gmail_now",
        "sync_drive",
    }
)


def scopes_for_role(role: UserRole | str) -> set[str]:
    rank = ROLE_HIERARCHY[UserRole(role)]
    return {scope for scope, minimum in SCOPE_MIN_ROLE.items() if rank >= ROLE_HIERARCHY[minimum]}


def min_role_for_tool(tool_name: str) -> UserRole | None:
    scope = TOOL_SCOPE.get(tool_name)
    return SCOPE_MIN_ROLE.get(scope) if scope else None
