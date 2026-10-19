"""Servidor MCP propio (FastMCP) para las tools de negocio de Inter
Rapidisimo. Expone por el protocolo MCP estandar las operaciones que ya
existen como endpoints REST -- usuarios, clientes, envios, reclamos,
base de conocimiento y las integraciones de Gmail y Google Drive -- para
que cualquier cliente MCP las pueda usar, incluido el propio agente de
chat (ver app.chat.mcp_tools; que herramientas ve cada rol lo decide
app.chat.tool_access).

Las integraciones (Gmail, Drive) NO se reemplazan por un servidor MCP
generico de terceros: esos son de un solo usuario y no tienen la
verificacion de remitente, la idempotencia ni el aislamiento por
organizacion que ya tiene el codigo propio. Se exponen aca, delgadas,
sobre esa misma API.

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

from mcp_server.auth import current_token, tool
from mcp_server.client import BackendAPIError, request

mcp = FastMCP("inter-rapidisimo-tools")


def _forwarded_headers() -> dict[str, str]:
    """El token delegado que mcp_server.auth.tool ya valido para esta
    llamada -- la API REST lo vuelve a validar (aud=mcp-server) y
    resuelve al usuario por `sub`."""
    return {"authorization": f"Bearer {current_token()}"}


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


def _drop_none(values: dict[str, Any]) -> dict[str, Any]:
    """Solo los parametros que el modelo realmente paso: un filtro en None
    no debe viajar a la API como "filtrar por vacio"."""
    return {key: value for key, value in values.items() if value is not None}


@tool(mcp, "profile:read")
async def whoami() -> Any:
    """Quien eres para el sistema: nombre, correo, rol y organizacion del
    usuario que habla contigo. Util para saber que puede hacer."""
    return await _call("GET", "/users/me")


@tool(mcp, "users:write")
async def create_user(email: str, full_name: str, role: str = "member") -> Any:
    """Invita a un nuevo usuario a la organizacion. Requiere rol
    ADMIN/OWNER. Si Gmail esta conectado para la organizacion, la
    contraseña temporal se manda por correo en vez de devolverse en la
    respuesta (ver password_sent_by_email)."""
    return await _call(
        "POST", "/users/", json={"email": email, "full_name": full_name, "role": role}
    )


@tool(mcp, "users:read")
async def list_users(skip: int = 0, limit: int = 50) -> Any:
    """Lista los usuarios de la organizacion. Requiere rol ADMIN/OWNER."""
    return await _call("GET", "/users/", params={"skip": skip, "limit": limit})


@tool(mcp, "users:read")
async def get_user(user_id: str) -> Any:
    """Consulta un usuario de la organizacion por su id."""
    return await _call("GET", f"/users/{user_id}")


@tool(mcp, "users:write")
async def update_user(
    user_id: str, email: str | None = None, full_name: str | None = None
) -> Any:
    """Actualiza el email y/o nombre de un usuario."""
    data = {k: v for k, v in {"email": email, "full_name": full_name}.items() if v is not None}
    return await _call("PATCH", f"/users/{user_id}", json=data)


@tool(mcp, "users:delete", confirm=True)
async def delete_user(user_id: str) -> Any:
    """Elimina (soft-delete) un usuario de la organizacion."""
    return await _call("DELETE", f"/users/{user_id}")


@tool(mcp, "users:roles", confirm=True)
async def change_user_role(user_id: str, role: str) -> Any:
    """Cambia el rol de un usuario (member/admin/viewer). El rol owner
    no se asigna por aca -- usa el flujo dedicado de transferencia de
    ownership."""
    return await _call("PATCH", f"/users/{user_id}/role", json={"role": role})


@tool(mcp, "shipments:read")
async def list_shipments(
    limit: int = 50,
    status: str | None = None,
    service_type: str | None = None,
    customer_id: str | None = None,
    city: str | None = None,
    q: str | None = None,
) -> Any:
    """Lista envios, mas reciente primero, con su cliente. Filtros
    opcionales: status (admitido, en_bodega_origen, en_transito,
    en_bodega_destino, en_reparto, entregado, novedad), service_type
    (estandar, express, same_day), customer_id, city (origen o destino) y
    q (guia o nombre del cliente). total_shipments cuenta los que cumplen
    los filtros."""
    params = _drop_none(
        {
            "limit": limit,
            "status": status,
            "service_type": service_type,
            "customer_id": customer_id,
            "city": city,
            "q": q,
        }
    )
    return await _call("GET", "/shipments/", params=params)


@tool(mcp, "shipments:read")
async def get_shipment_status(tracking_number: str) -> Any:
    """Consulta el estado actual de un envio por su numero de guia,
    incluyendo ciudad y fecha de su ultimo movimiento."""
    return await _call("GET", f"/shipments/{tracking_number}/status")


@tool(mcp, "shipments:read")
async def get_shipment_history(tracking_number: str) -> Any:
    """Linea de tiempo completa de un envio: cada evento con su estado,
    ciudad, nota y fecha, del mas antiguo al mas reciente."""
    return await _call("GET", f"/shipments/{tracking_number}/history")


@tool(mcp, "shipments:read")
async def shipment_stats() -> Any:
    """Panorama de la operacion: total de envios y clientes, cuantos hay
    por estado y por servicio, y cuantos requieren atencion segun la
    politica de demoras (seguimiento preventivo, demorados, demora extrema)."""
    return await _call("GET", "/shipments/stats")


@tool(mcp, "shipments:read")
async def list_delayed_shipments(level: str = "delayed", limit: int = 50) -> Any:
    """Envios demorados segun la politica de la base de conocimiento
    (umbrales por zona urbana/nacional/rural, en dias habiles sin evento).
    level: preventive (incluye los de seguimiento preventivo), delayed o
    extreme (mas de 10 dias habiles sin movimiento)."""
    return await _call("GET", "/shipments/delayed", params={"level": level, "limit": limit})


@tool(mcp, "customers:read")
async def list_customers(limit: int = 50, search: str | None = None) -> Any:
    """Lista los clientes de la organizacion. search busca por nombre,
    correo, documento o telefono (sin importar tildes ni mayusculas)."""
    return await _call(
        "GET", "/shipments/customers", params=_drop_none({"limit": limit, "search": search})
    )


@tool(mcp, "customers:read")
async def get_customer(customer_id: str) -> Any:
    """Un cliente por su id, con sus envios mas recientes."""
    return await _call("GET", f"/shipments/customers/{customer_id}")


@tool(mcp, "customers:write")
async def create_customer(
    full_name: str,
    email: str | None = None,
    phone: str | None = None,
    document_id: str | None = None,
) -> Any:
    """Registra un cliente nuevo. Un correo o documento que ya pertenece
    a otro cliente se rechaza. Requiere cualquier rol excepto VIEWER."""
    body = _drop_none(
        {"full_name": full_name, "email": email, "phone": phone, "document_id": document_id}
    )
    return await _call("POST", "/shipments/customers", json=body)


@tool(mcp, "customers:write")
async def update_customer(
    customer_id: str,
    full_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    document_id: str | None = None,
) -> Any:
    """Actualiza datos de contacto de un cliente (solo lo que se pase).
    Requiere cualquier rol excepto VIEWER."""
    body = _drop_none(
        {"full_name": full_name, "email": email, "phone": phone, "document_id": document_id}
    )
    return await _call("PATCH", f"/shipments/customers/{customer_id}", json=body)


@tool(mcp, "shipments:write")
async def create_shipment(
    customer_id: str,
    origin_city: str,
    destination_city: str,
    service_type: str = "estandar",
    tracking_number: str | None = None,
) -> Any:
    """Admite un envio nuevo para un cliente existente. Si no se pasa
    tracking_number se genera uno. service_type: estandar, express o
    same_day (solo dentro de la misma ciudad; express no llega a zonas
    rurales). Requiere cualquier rol excepto VIEWER."""
    body = _drop_none(
        {
            "customer_id": customer_id,
            "origin_city": origin_city,
            "destination_city": destination_city,
            "service_type": service_type,
            "tracking_number": tracking_number,
        }
    )
    return await _call("POST", "/shipments/", json=body)


@tool(mcp, "shipments:notify", confirm=True)
async def notify_shipment_customer(tracking_number: str, subject: str, message: str) -> Any:
    """Envia un correo al cliente de ese envio (siempre a su correo
    registrado; no se puede elegir otro destinatario) desde el Gmail
    conectado. Maximo 3 avisos por envio cada 24 horas. Mostrale al
    usuario el texto exacto y espera su aprobacion antes de llamar."""
    return await _call(
        "POST",
        f"/shipments/{tracking_number}/notify",
        json={"subject": subject, "message": message},
    )


@tool(mcp, "claims:write")
async def open_claim(tracking_number: str, claim_type: str, description: str) -> Any:
    """Abre un reclamo (investigacion) sobre un envio y devuelve su numero
    y plazo (SLA). claim_type: dano, perdida, retraso, cobro_incorrecto o
    entrega_no_autorizada. Un envio no puede tener dos reclamos activos
    del mismo tipo. Requiere cualquier rol excepto VIEWER."""
    return await _call(
        "POST",
        "/claims/",
        json={"tracking_number": tracking_number, "claim_type": claim_type, "description": description},
    )


@tool(mcp, "claims:read")
async def get_claim(claim_number: str) -> Any:
    """Un reclamo por su numero (CLM-AAAA-NNNNNN), con su historial."""
    return await _call("GET", f"/claims/{claim_number}")


@tool(mcp, "claims:read")
async def list_claims(
    status: str | None = None, tracking_number: str | None = None, limit: int = 50
) -> Any:
    """Lista reclamos, el mas reciente primero. Filtros: status (abierto,
    en_investigacion, resuelto, cerrado) y tracking_number."""
    return await _call(
        "GET",
        "/claims/",
        params=_drop_none({"limit": limit, "status": status, "tracking_number": tracking_number}),
    )


@tool(mcp, "claims:write")
async def update_claim_status(claim_number: str, new_status: str, note: str | None = None) -> Any:
    """Cambia el estado de un reclamo. Flujo: abierto -> en_investigacion
    -> resuelto -> cerrado (un resuelto puede reabrirse a en_investigacion;
    cerrado es final). Para resuelto, `note` es obligatoria y explica la
    resolucion. Requiere cualquier rol excepto VIEWER."""
    return await _call(
        "PATCH",
        f"/claims/{claim_number}/status",
        json=_drop_none({"new_status": new_status, "note": note}),
    )


@tool(mcp, "kb:read")
async def search_knowledge_base(query: str, top_k: int = 5) -> Any:
    """Busca en la base de conocimiento (politicas, tarifas, oficinas,
    codigos de novedad, procedimientos). Usala cuando el contexto inicial
    no alcance: reformula con otras palabras (un codigo, una ciudad, el
    nombre de la politica). Devuelve fragmentos con su articulo; una lista
    vacia significa que no hay nada relevante."""
    return await _call("POST", "/kb/search", json={"query": query, "top_k": top_k})


@tool(mcp, "kb:admin")
async def list_kb_documents(limit: int = 50) -> Any:
    """Documentos de la base de conocimiento: titulo, origen (archivo,
    subida, Drive), nivel de acceso y cuando se indexo. Requiere ADMIN/OWNER."""
    return await _call("GET", "/kb/documents", params={"limit": limit})


@tool(mcp, "gmail:read")
async def gmail_status() -> Any:
    """Si Gmail esta conectado para la organizacion, con que cuenta y
    cuando fue la ultima revision del buzon. Requiere ADMIN/OWNER."""
    return await _call("GET", "/gmail/status")


@tool(mcp, "gmail:read")
async def list_unresolved_emails(limit: int = 50) -> Any:
    """Correos de clientes que llegaron y NO tuvieron una respuesta util
    (guia inexistente, remitente no verificado, fallo al responder), para
    que una persona los revise. Requiere ADMIN/OWNER."""
    return await _call("GET", "/gmail/unresolved", params={"limit": limit})


@tool(mcp, "gmail:poll", confirm=True)
async def poll_gmail_now() -> Any:
    """Revisa el buzon de Gmail ahora mismo y responde las consultas de
    seguimiento pendientes (normalmente lo hace un proceso en segundo
    plano cada minuto). Devuelve cuantos correos examino, respondio,
    omitio y fallaron. Requiere ADMIN/OWNER."""
    return await _call("POST", "/gmail/poll")


@tool(mcp, "drive:read")
async def drive_status() -> Any:
    """Si Google Drive esta conectado, la carpeta que se sincroniza con la
    base de conocimiento y cuando fue la ultima sincronizacion. Requiere
    ADMIN/OWNER."""
    return await _call("GET", "/drive/status")


@tool(mcp, "drive:sync", confirm=True)
async def sync_drive() -> Any:
    """Sincroniza la carpeta de Google Drive con la base de conocimiento:
    indexa los documentos nuevos o modificados y retira los que ya no
    estan. Devuelve cuantos documentos proceso y retiro. Requiere ADMIN/OWNER."""
    return await _call("POST", "/drive/sync")


@tool(mcp, "shipments:write")
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
