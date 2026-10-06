"""Tools de envios y clientes para el agente de chat (ver
app.llm.tools.run_tool_loop / run_streaming_tool_loop).

Lectura (list_shipments, get_shipment_status, list_customers): sin
jerarquia de roles que aplicar -- envios y clientes son datos de
negocio del tenant, no cuentas de la plataforma, asi que cualquier
usuario autenticado de la organizacion puede consultarlos (igual que
ya puede via el RAG sobre la KB de politicas). Lo unico que se aplica
es aislamiento por organization_id, cerrado sobre `actor` igual que
build_user_management_tools.

Escritura (update_shipment_status): requiere no ser VIEWER. Decision
explicita, no un descuido -- criterio: actualizar el estado de un
envio es una operacion de negocio rutinaria (un mensajero o alguien de
bodega marcando "entregado", no un administrador de cuentas), asi que
exigir ADMIN/OWNER como en app.users.tools seria sobre-restringir un
caso de uso real ("el usuario X actualiza el envio 854321 a
entregado") a un rol equivocado -- ADMIN/OWNER es sobre gestionar la
cuenta de la organizacion en la plataforma, no sobre operar la
logistica del dia a dia. VIEWER si queda afuera porque su unico
proposito ya establecido (ver ROLE_HIERARCHY) es "solo lectura"; un
VIEWER que puede escribir dejaria de ser un viewer. Si mas adelante
aparece un motivo de negocio para acotar esto mas (ej. solo quien
gestiona la relacion con ese cliente), este es el lugar para
endurecerlo -- no antes de que exista ese requisito real.
"""

from __future__ import annotations

from typing import Any

from app.llm.schemas import ToolDefinition
from app.llm.tools import Tool
from app.shipments.models import Customer, Shipment, ShipmentStatus
from app.shipments.service import ShipmentService
from app.users.models import User, UserRole


def _require_not_viewer(actor: User) -> None:
    if actor.role == UserRole.VIEWER:
        raise PermissionError(
            "Viewer accounts cannot update shipments, only view them"
        )


def _serialize_customer(customer: Customer) -> dict[str, Any]:
    return {
        "id": str(customer.id),
        "full_name": customer.full_name,
        "document_id": customer.document_id,
        "phone": customer.phone,
        "email": customer.email,
    }


def _serialize_shipment_summary(shipment: Shipment) -> dict[str, Any]:
    """Para list_shipments: un renglon por envio, con el nombre del
    cliente ya resuelto -- exactamente lo que hace falta para
    responder "cuantos envios y a que clientes pertenecen" sin que el
    LLM tenga que pedir cada cliente por separado."""
    return {
        "tracking_number": shipment.tracking_number,
        "customer_name": shipment.customer.full_name,
        "status": shipment.status.value,
        "service_type": shipment.service_type.value,
        "origin_city": shipment.origin_city,
        "destination_city": shipment.destination_city,
        "created_at": shipment.created_at.isoformat(),
    }


def _serialize_shipment_detail(shipment: Shipment) -> dict[str, Any]:
    # events esta ordenado ascendente por occurred_at (ver
    # Shipment.events en app.shipments.models) -- el ultimo elemento es
    # el movimiento mas reciente, la fuente de verdad de "donde esta"
    # segun el docstring del modulo, no Shipment.created_at.
    last_event = shipment.events[-1] if shipment.events else None
    return {
        "tracking_number": shipment.tracking_number,
        "status": shipment.status.value,
        "service_type": shipment.service_type.value,
        "origin_city": shipment.origin_city,
        "destination_city": shipment.destination_city,
        "customer": _serialize_customer(shipment.customer),
        "last_event_city": last_event.city if last_event else None,
        "last_event_at": last_event.occurred_at.isoformat() if last_event else None,
        "last_event_notes": last_event.notes if last_event else None,
        "created_at": shipment.created_at.isoformat(),
    }


_STATUS_VALUES = [s.value for s in ShipmentStatus]


def build_shipment_tools(actor: User, service: ShipmentService) -> list[Tool]:
    """Arma las tools de envios/clientes cerradas sobre `actor` -- el
    usuario autenticado que abrio esta conversacion de chat. Las tools
    de lectura se ofrecen a cualquiera; update_shipment_status hace su
    propio chequeo de rol (ver el docstring del modulo) y devuelve un
    error claro si `actor` no puede hacer esa operacion, mismo patron
    que build_user_management_tools."""

    async def list_shipments(limit: int = 50) -> Any:
        limit = max(1, min(limit, 100))
        shipments, total = await service.list_shipments(actor.organization_id, 0, limit)
        return {
            "total_shipments": total,
            "returned": len(shipments),
            "shipments": [_serialize_shipment_summary(s) for s in shipments],
        }

    async def get_shipment_status(tracking_number: str) -> Any:
        shipment = await service.get_by_tracking_number(actor.organization_id, tracking_number)
        if shipment is None:
            return {"error": f"No se encontro un envio con guia '{tracking_number}'"}
        return _serialize_shipment_detail(shipment)

    async def list_customers(limit: int = 50) -> Any:
        limit = max(1, min(limit, 100))
        customers, total = await service.list_customers(actor.organization_id, 0, limit)
        return {
            "total_customers": total,
            "returned": len(customers),
            "customers": [_serialize_customer(c) for c in customers],
        }

    async def update_shipment_status(
        tracking_number: str,
        new_status: str,
        city: str | None = None,
        notes: str | None = None,
    ) -> Any:
        _require_not_viewer(actor)
        try:
            status = ShipmentStatus(new_status.lower())
        except ValueError:
            return {
                "error": f"Estado '{new_status}' inválido. Usa uno de: {', '.join(_STATUS_VALUES)}"
            }
        shipment = await service.update_shipment_status(
            actor.organization_id,
            tracking_number,
            status,
            city=city,
            notes=notes,
        )
        if shipment is None:
            return {"error": f"No se encontro un envio con guia '{tracking_number}'"}
        return _serialize_shipment_detail(shipment)

    return [
        Tool(
            definition=ToolDefinition(
                name="list_shipments",
                description=(
                    "List shipments for this organization, most recent first, "
                    "including which customer each one belongs to. Use this "
                    "for questions like 'how many shipments have we made' or "
                    "'which customers have shipments'. total_shipments in the "
                    "result is the real total even if fewer rows are returned."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Max shipments to return (1-100, default 50)",
                        }
                    },
                    "required": [],
                },
            ),
            handler=list_shipments,
        ),
        Tool(
            definition=ToolDefinition(
                name="get_shipment_status",
                description=(
                    "Get the current status of a single shipment by its "
                    "tracking number, including the city and date of its "
                    "most recent event and which customer it belongs to."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "tracking_number": {
                            "type": "string",
                            "description": "The shipment's tracking number",
                        }
                    },
                    "required": ["tracking_number"],
                },
            ),
            handler=get_shipment_status,
        ),
        Tool(
            definition=ToolDefinition(
                name="list_customers",
                description=(
                    "List customers registered for this organization. Use "
                    "this for questions like 'how many customers do we have' "
                    "or to look up a customer's contact details."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Max customers to return (1-100, default 50)",
                        }
                    },
                    "required": [],
                },
            ),
            handler=list_customers,
        ),
        Tool(
            definition=ToolDefinition(
                name="update_shipment_status",
                description=(
                    "Update a shipment's status by tracking number, recording "
                    "a new tracking event (city/notes optional). Requires any "
                    "role except VIEWER. Use this for things like 'mark "
                    "shipment 854321 as delivered'. If the new status is "
                    "'novedad', always pass notes explaining what happened -- "
                    "a novedad without an explanation isn't useful to anyone "
                    "reading the history later."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "tracking_number": {
                            "type": "string",
                            "description": "The shipment's tracking number",
                        },
                        "new_status": {
                            "type": "string",
                            "enum": _STATUS_VALUES,
                            "description": "New status for the shipment",
                        },
                        "city": {
                            "type": "string",
                            "description": "City where this event occurred, if relevant",
                        },
                        "notes": {
                            "type": "string",
                            "description": "Extra context for this event, required in practice for 'novedad'",
                        },
                    },
                    "required": ["tracking_number", "new_status"],
                },
            ),
            handler=update_shipment_status,
        ),
    ]
