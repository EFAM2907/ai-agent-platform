"""Que herramientas MCP ve cada rol dentro del chat.

La API REST (detras del servidor MCP) es la que AUTORIZA de verdad cada
operacion: si un viewer llamara a `create_user`, recibiria un 403. Este
filtro no reemplaza eso -- lo complementa:

1. Menos tokens: el schema de cada herramienta viaja en CADA llamada al
   modelo. Un viewer no necesita ver las ~30 herramientas de un admin.
2. Menos errores del modelo: ofrecerle una herramienta que va a ser
   rechazada solo lo invita a intentarla y a explicar mal el rechazo.

Una herramienta que NO figura aca solo se ofrece a ADMIN/OWNER (y se
loguea un warning): olvidar registrar una herramienta nueva debe
fallar hacia "menos visible", no hacia "visible para todos". El test
tests/test_tool_access.py verifica que toda herramienta del servidor
MCP este registrada.
"""

from __future__ import annotations

import logging

from app.llm.tools import Tool
from app.core.scopes import SCOPE_MIN_ROLE, TOOL_SCOPE, scopes_for_role
from app.users.models import ROLE_HIERARCHY, UserRole

logger = logging.getLogger(__name__)

# nombre de la herramienta -> rol minimo, DERIVADO de TOOL_SCOPE/SCOPE_MIN_ROLE
# (app.core.scopes): el scope es la fuente de verdad, el rol minimo es
# solo su consecuencia.
TOOL_MIN_ROLE: dict[str, UserRole] = {
    name: SCOPE_MIN_ROLE[scope] for name, scope in TOOL_SCOPE.items()
}


def scopes_for_user(role: UserRole | str) -> set[str]:
    """Scopes que ChatService pide al STS en el token del turno."""
    return scopes_for_role(role)


def filter_tools_for_role(tools: list[Tool], role: UserRole | str) -> list[Tool]:
    """Las herramientas que `role` puede ver, en el mismo orden."""
    rank = ROLE_HIERARCHY[UserRole(role)]
    visible: list[Tool] = []
    for tool in tools:
        minimum = TOOL_MIN_ROLE.get(tool.name)
        if minimum is None:
            logger.warning(
                "La herramienta MCP '%s' no esta en TOOL_MIN_ROLE: solo se ofrece a ADMIN/OWNER",
                tool.name,
            )
            minimum = UserRole.ADMIN
        if rank >= ROLE_HIERARCHY[minimum]:
            visible.append(tool)
    return visible
