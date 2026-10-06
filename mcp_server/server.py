"""Servidor MCP propio (FastMCP) para las tools de negocio de Inter
Rapidisimo -- usuarios y envios. Expone por el protocolo MCP estandar
las mismas 10 operaciones que ya existen como endpoints REST
(app.users.api, app.shipments.api), para que cualquier cliente MCP las
pueda usar -- incluido el propio agente de chat (ver
app.chat.mcp_tools), que ya no las llama in-process (ver
docs/roadmap.md seccion 2, "Construir un MCP propio con FastMCP").

Diseno "proxy delgado": cada tool reenvia la llamada como un request
HTTP normal a la API REST que ya existe (app.users.api,
app.shipments.api) en vez de importar SQLAlchemy/los services y hablar
directo con la base. Dos ganancias concretas de esa decision:

1. El contenedor de este servicio queda liviano -- sin SQLAlchemy,
   asyncpg, ni cryptography, que son las dependencias que mas pesan en
   la imagen de la API principal.
2. La identidad del usuario cruza el proceso gratis: se reenvia el
   mismo Bearer token que el cliente MCP ya trae en su request HTTP
   (ver _forwarded_headers), y la API principal lo valida exactamente
   igual que a cualquier llamada de su propio frontend. Este servidor
   NO tiene logica de autorizacion propia -- si el token es invalido o
   el usuario no tiene permiso para esa operacion, la API principal
   devuelve 401/403/404 y eso es lo que la tool le reenvia al LLM.

Transporte: streamable-http unicamente. get_http_headers() depende de
que haya un request HTTP real detras de la llamada -- no funciona con
transporte stdio (sin servidor HTTP de por medio), que quedaria sin
forma de recibir el token del usuario con este diseno.
"""

from __future__ import annotations

import os
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

from mcp_server.client import BackendAPIError, request

mcp = FastMCP("inter-rapidisimo-tools")


def _forwarded_headers() -> dict[str, str]:
    headers = get_http_headers(include={"authorization"})
    if "authorization" not in headers:
        raise PermissionError(
            "Falta el header Authorization -- este servidor MCP no acepta "
            "llamadas sin el Bearer token del usuario"
        )
    return headers


async def _call(method: str, path: str, **kwargs: Any) -> Any:
    """Reenvia a la API principal y traduce un error HTTP a un dict
    {"error", "status_code"} para que un fallo de negocio (404, 403,
    400) sea un resultado que el LLM puede leer y explicarle al
    usuario (ver app.llm.tools._execute_tool, que atrapa cualquier
    excepcion de una tool de la misma forma), no una excepcion que
    tumbe el turno de chat completo.
    Un header Authorization faltante (PermissionError) NO se atrapa
    aca a proposito -- es un error de configuracion del cliente MCP,
    no un resultado de negocio, y debe propagarse como falla real de
    la tool call."""
    try:
        return await request(method, path, headers=_forwarded_headers(), **kwargs)
    except BackendAPIError as exc:
        return {"error": exc.detail, "status_code": exc.status_code}


@mcp.tool
async def create_user(email: str, full_name: str, role: str = "member") -> Any:
    """Invita a un nuevo usuario a la organizacion. Requiere rol
    ADMIN/OWNER. Si Gmail esta conectado para la organizacion, la
    contraseña temporal se manda por correo en vez de devolverse en la
    respuesta (ver password_sent_by_email)."""
    return await _call(
        "POST", "/users/", json={"email": email, "full_name": full_name, "role": role}
    )


@mcp.tool
async def list_users(skip: int = 0, limit: int = 50) -> Any:
    """Lista los usuarios de la organizacion. Requiere rol ADMIN/OWNER."""
    return await _call("GET", "/users/", params={"skip": skip, "limit": limit})


@mcp.tool
async def get_user(user_id: str) -> Any:
    """Consulta un usuario de la organizacion por su id."""
    return await _call("GET", f"/users/{user_id}")


@mcp.tool
async def update_user(
    user_id: str, email: str | None = None, full_name: str | None = None
) -> Any:
    """Actualiza el email y/o nombre de un usuario."""
    data = {k: v for k, v in {"email": email, "full_name": full_name}.items() if v is not None}
    return await _call("PATCH", f"/users/{user_id}", json=data)


@mcp.tool
async def delete_user(user_id: str) -> Any:
    """Elimina (soft-delete) un usuario de la organizacion."""
    return await _call("DELETE", f"/users/{user_id}")


@mcp.tool
async def change_user_role(user_id: str, role: str) -> Any:
    """Cambia el rol de un usuario (member/admin/viewer). El rol owner
    no se asigna por aca -- usa el flujo dedicado de transferencia de
    ownership."""
    return await _call("PATCH", f"/users/{user_id}/role", json={"role": role})


@mcp.tool
async def list_shipments(limit: int = 50) -> Any:
    """Lista los envios de la organizacion, mas reciente primero,
    junto con el cliente al que pertenece cada uno."""
    return await _call("GET", "/shipments/", params={"limit": limit})


@mcp.tool
async def get_shipment_status(tracking_number: str) -> Any:
    """Consulta el estado actual de un envio por su numero de guia,
    incluyendo ciudad y fecha de su ultimo movimiento."""
    return await _call("GET", f"/shipments/{tracking_number}/status")


@mcp.tool
async def list_customers(limit: int = 50) -> Any:
    """Lista los clientes registrados de la organizacion."""
    return await _call("GET", "/shipments/customers", params={"limit": limit})


@mcp.tool
async def update_shipment_status(
    tracking_number: str,
    new_status: str,
    city: str | None = None,
    notes: str | None = None,
) -> Any:
    """Actualiza el estado de un envio, registrando un nuevo evento de
    tracking. Requiere cualquier rol excepto VIEWER. Si new_status es
    'novedad', pasa siempre notes explicando que paso."""
    return await _call(
        "PATCH",
        f"/shipments/{tracking_number}/status",
        json={"new_status": new_status, "city": city, "notes": notes},
    )


if __name__ == "__main__":
    port = int(os.environ.get("MCP_SERVER_PORT", "8100"))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
