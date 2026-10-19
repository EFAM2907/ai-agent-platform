"""Schemas de respuesta de envios/clientes, usados por app.shipments.api
(REST) -- un solo lugar para "como se ve un envio hacia afuera". El
chat ya no las usa directo (ver docs/roadmap.md seccion 2): sus tools
de envios pasan por el servidor MCP, que reenvia a este mismo REST, asi
que de todas formas terminan pasando por aca -- un solo lugar decide la
forma, no dos copias que se puedan desincronizar.

Las funciones serialize_* devuelven dict (no una instancia de
BaseModel) a proposito: app.shipments.api las usa para construir la
respuesta tipada (ShipmentDetailResponse(**serialize_shipment_detail(s))),
pero el dict plano en si (texto/numeros/listas, sin envolver en un
objeto Pydantic) es tambien la forma mas facil de pasarle a cualquier
otro consumidor que no necesite el tipado de FastAPI."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field

from app.shipments.models import Customer, Shipment, ServiceType, ShipmentStatus


def serialize_customer(customer: Customer) -> dict[str, Any]:
    return {
        "id": str(customer.id),
        "full_name": customer.full_name,
        "document_id": customer.document_id,
        "phone": customer.phone,
        "email": customer.email,
    }


def serialize_shipment_summary(shipment: Shipment) -> dict[str, Any]:
    """Para list_shipments/GET /shipments: un renglon por envio, con el
    nombre del cliente ya resuelto -- exactamente lo que hace falta
    para responder "cuantos envios y a que clientes pertenecen" sin
    pedir cada cliente por separado."""
    return {
        "tracking_number": shipment.tracking_number,
        "customer_name": shipment.customer.full_name,
        "status": shipment.status.value,
        "service_type": shipment.service_type.value,
        "origin_city": shipment.origin_city,
        "destination_city": shipment.destination_city,
        "created_at": shipment.created_at.isoformat(),
    }


def serialize_shipment_detail(shipment: Shipment) -> dict[str, Any]:
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
        "customer": serialize_customer(shipment.customer),
        "last_event_city": last_event.city if last_event else None,
        "last_event_at": last_event.occurred_at.isoformat() if last_event else None,
        "last_event_notes": last_event.notes if last_event else None,
        "created_at": shipment.created_at.isoformat(),
    }


class CustomerResponse(BaseModel):
    id: uuid.UUID
    full_name: str
    document_id: str | None = None
    phone: str | None = None
    email: str | None = None


class ShipmentSummaryResponse(BaseModel):
    """Un renglon de GET /shipments -- mismo shape que
    serialize_shipment_summary."""

    tracking_number: str
    customer_name: str
    status: ShipmentStatus
    service_type: ServiceType
    origin_city: str
    destination_city: str
    created_at: datetime


class ShipmentListResponse(BaseModel):
    total_shipments: int
    returned: int
    shipments: list[ShipmentSummaryResponse]


class CustomerListResponse(BaseModel):
    total_customers: int
    returned: int
    customers: list[CustomerResponse]


class ShipmentDetailResponse(BaseModel):
    """Mismo shape que serialize_shipment_detail -- last_event_* se
    derivan de events[-1] (el mas reciente), no de un campo
    denormalizado en Shipment."""

    tracking_number: str
    status: ShipmentStatus
    service_type: ServiceType
    origin_city: str
    destination_city: str
    customer: CustomerResponse
    last_event_city: str | None = None
    last_event_at: datetime | None = None
    last_event_notes: str | None = None
    created_at: datetime


class ShipmentStatusUpdate(BaseModel):
    """new_status como string libre (no ShipmentStatus directo) a
    proposito -- igual que la tool de chat update_shipment_status: asi
    el endpoint puede devolver un 400 con el mensaje de error claro
    (estados validos) en vez de que FastAPI lo rechace con un 422
    generico de validacion de enum antes de que el endpoint decida el
    mensaje."""

    new_status: str
    city: str | None = None
    notes: str | None = None


# ---------------------------------------------------------------------
# Escritura, historial, demoras, estadisticas y notificaciones
# ---------------------------------------------------------------------
class CustomerCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    document_id: str | None = Field(default=None, max_length=50)


class CustomerUpdate(BaseModel):
    """Todos opcionales: solo se cambia lo que viene en el body."""

    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    document_id: str | None = Field(default=None, max_length=50)


class ShipmentCreate(BaseModel):
    customer_id: uuid.UUID
    origin_city: str = Field(min_length=2, max_length=100)
    destination_city: str = Field(min_length=2, max_length=100)
    # string libre por la misma razon que ShipmentStatusUpdate.new_status
    service_type: str = "estandar"
    tracking_number: str | None = Field(default=None, max_length=50)


class ShipmentEventResponse(BaseModel):
    event_type: ShipmentStatus
    city: str | None = None
    notes: str | None = None
    occurred_at: datetime


class ShipmentHistoryResponse(BaseModel):
    tracking_number: str
    status: ShipmentStatus
    customer_name: str
    origin_city: str
    destination_city: str
    events: list[ShipmentEventResponse]


class DelayedShipmentResponse(BaseModel):
    tracking_number: str
    customer_name: str
    status: ShipmentStatus
    origin_city: str
    destination_city: str
    last_event_at: datetime | None = None
    level: str
    business_days_without_event: int
    delayed_after: int
    zone: str


class DelayedShipmentListResponse(BaseModel):
    returned: int
    shipments: list[DelayedShipmentResponse]


class ShipmentStatsResponse(BaseModel):
    total_shipments: int
    total_customers: int
    by_status: dict[str, int]
    by_service: dict[str, int]
    attention: dict[str, int]


class CustomerDetailResponse(CustomerResponse):
    shipments: list[ShipmentSummaryResponse] = []


class NotifyRequest(BaseModel):
    subject: str = Field(min_length=3, max_length=200)
    message: str = Field(min_length=10, max_length=2000)


class NotifyResponse(BaseModel):
    tracking_number: str
    sent_to: str
    subject: str
