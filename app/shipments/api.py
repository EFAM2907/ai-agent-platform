"""Endpoints REST de envios/clientes. Las tools de chat del servidor MCP
(list_shipments, create_shipment, update_shipment_status, ...) reenvian
aca en vez de llamar ShipmentService directo -- este es el UNICO lugar
que aplica las reglas de permisos:

- lectura abierta a cualquier usuario autenticado del tenant
  (envios/clientes son datos de negocio, no cuentas de la plataforma);
- escritura (crear/editar clientes, crear envios, cambiar estado,
  avisar al cliente) vetada solo para VIEWER.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.rate_limit import rate_limit
from app.gmail.errors import GmailError, GmailNotConnectedError
from app.gmail.repository import GmailRepository
from app.gmail.service import GmailConnectionService
from app.organizations.dependencies import get_current_organization
from app.organizations.models import Organization
from app.shipments.delays import LEVEL_DELAYED, LEVEL_EXTREME, LEVEL_PREVENTIVE
from app.shipments.exceptions import NotificationNotPossibleError, ShipmentError
from app.shipments.models import ServiceType, ShipmentStatus
from app.shipments.repository import ShipmentFilters, ShipmentRepository
from app.shipments.schemas import (
    CustomerCreate,
    CustomerDetailResponse,
    CustomerListResponse,
    CustomerResponse,
    CustomerUpdate,
    DelayedShipmentListResponse,
    DelayedShipmentResponse,
    NotifyRequest,
    NotifyResponse,
    ShipmentCreate,
    ShipmentDetailResponse,
    ShipmentEventResponse,
    ShipmentHistoryResponse,
    ShipmentListResponse,
    ShipmentStatsResponse,
    ShipmentStatusUpdate,
    serialize_customer,
    serialize_shipment_detail,
    serialize_shipment_summary,
)
from app.shipments.service import ShipmentService
from app.users.models import User, UserRole

logger = logging.getLogger(__name__)


def get_shipment_service(session: AsyncSession = Depends(get_db)) -> ShipmentService:
    return ShipmentService(ShipmentRepository(session), session)


def get_gmail_service(session: AsyncSession = Depends(get_db)) -> GmailConnectionService:
    return GmailConnectionService(GmailRepository(session), session)


router = APIRouter(prefix="/shipments", tags=["shipments"], dependencies=[Depends(rate_limit)])


def _require_can_write(user: User, action: str) -> None:
    if user.role == UserRole.VIEWER:
        raise HTTPException(
            status_code=403, detail=f"Viewer accounts cannot {action}, only view"
        )


def _http_error(exc: ShipmentError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _parse_enum(enum_cls, value: str, label: str):
    try:
        return enum_cls(value.strip().lower())
    except ValueError:
        valid = ", ".join(item.value for item in enum_cls)
        raise HTTPException(status_code=400, detail=f"{label} '{value}' invalido. Usa uno de: {valid}")


# ---------------------------------------------------------------------
# Envios: lectura
# ---------------------------------------------------------------------
@router.get("/", response_model=ShipmentListResponse)
async def list_shipments(
    skip: int = 0,
    limit: int = Query(default=50, le=100),
    status: str | None = None,
    service_type: str | None = None,
    customer_id: uuid.UUID | None = None,
    city: str | None = Query(default=None, max_length=100),
    q: str | None = Query(default=None, max_length=100),
    service: ShipmentService = Depends(get_shipment_service),
    current_user: User = Depends(get_current_user),
):
    """Filtros opcionales: estado, servicio, cliente, ciudad (origen o
    destino) y `q` (guia o nombre del cliente). total_shipments cuenta los
    que cumplen los filtros, no todos los del tenant."""
    filters = ShipmentFilters(
        status=_parse_enum(ShipmentStatus, status, "Estado") if status else None,
        service_type=_parse_enum(ServiceType, service_type, "Servicio") if service_type else None,
        customer_id=customer_id,
        city=city,
        query=q,
    )
    shipments, total = await service.list_shipments(
        current_user.organization_id, skip, limit, filters
    )
    return ShipmentListResponse(
        total_shipments=total,
        returned=len(shipments),
        shipments=[serialize_shipment_summary(s) for s in shipments],
    )


@router.get("/stats", response_model=ShipmentStatsResponse)
async def shipment_stats(
    service: ShipmentService = Depends(get_shipment_service),
    current_user: User = Depends(get_current_user),
):
    """Panorama de la operacion: totales, por estado y por servicio, y
    cuantos envios requieren atencion segun la politica de demoras."""
    return ShipmentStatsResponse(**await service.stats(current_user.organization_id))


@router.get("/delayed", response_model=DelayedShipmentListResponse)
async def delayed_shipments(
    level: str = Query(default=LEVEL_DELAYED, pattern="^(preventive|delayed|extreme)$"),
    limit: int = Query(default=50, le=100),
    service: ShipmentService = Depends(get_shipment_service),
    current_user: User = Depends(get_current_user),
):
    """Envios que la politica de la KB considera demorados. level=
    preventive incluye tambien los que solo necesitan seguimiento
    preventivo; extreme, solo los de mas de 10 dias habiles sin
    movimiento."""
    rows = await service.delayed_shipments(
        current_user.organization_id, minimum_level=level, limit=limit
    )
    items = []
    for shipment, assessment in rows:
        last_event = max((e.occurred_at for e in shipment.events), default=None)
        items.append(
            DelayedShipmentResponse(
                tracking_number=shipment.tracking_number,
                customer_name=shipment.customer.full_name,
                status=shipment.status,
                origin_city=shipment.origin_city,
                destination_city=shipment.destination_city,
                last_event_at=last_event,
                level=assessment["level"],
                business_days_without_event=assessment["business_days_without_event"],
                delayed_after=assessment["delayed_after"],
                zone=assessment["zone"],
            )
        )
    return DelayedShipmentListResponse(returned=len(items), shipments=items)


# ---------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------
# Declarados antes de /{tracking_number}/... a proposito, mismo motivo que
# GET /users/me antes de GET /users/{user_id}.
@router.get("/customers", response_model=CustomerListResponse)
async def list_customers(
    skip: int = 0,
    limit: int = Query(default=50, le=100),
    search: str | None = Query(default=None, max_length=100),
    service: ShipmentService = Depends(get_shipment_service),
    current_user: User = Depends(get_current_user),
):
    """`search` busca por nombre, correo, documento o telefono."""
    customers, total = await service.list_customers(
        current_user.organization_id, skip, limit, search
    )
    return CustomerListResponse(
        total_customers=total,
        returned=len(customers),
        customers=[serialize_customer(c) for c in customers],
    )


@router.post("/customers", response_model=CustomerResponse, status_code=201)
async def create_customer(
    data: CustomerCreate,
    service: ShipmentService = Depends(get_shipment_service),
    current_user: User = Depends(get_current_user),
):
    _require_can_write(current_user, "create customers")
    try:
        customer = await service.create_customer(
            current_user.organization_id,
            full_name=data.full_name,
            email=data.email,
            phone=data.phone,
            document_id=data.document_id,
        )
    except ShipmentError as exc:
        raise _http_error(exc)
    return CustomerResponse(**serialize_customer(customer))


@router.get("/customers/{customer_id}", response_model=CustomerDetailResponse)
async def get_customer(
    customer_id: uuid.UUID,
    service: ShipmentService = Depends(get_shipment_service),
    current_user: User = Depends(get_current_user),
):
    try:
        customer = await service.get_customer(current_user.organization_id, customer_id)
    except ShipmentError as exc:
        raise _http_error(exc)
    shipments = await service.repository.customer_shipments(
        current_user.organization_id, customer.id
    )
    return CustomerDetailResponse(
        **serialize_customer(customer),
        shipments=[serialize_shipment_summary(s) for s in shipments],
    )


@router.patch("/customers/{customer_id}", response_model=CustomerResponse)
async def update_customer(
    customer_id: uuid.UUID,
    data: CustomerUpdate,
    service: ShipmentService = Depends(get_shipment_service),
    current_user: User = Depends(get_current_user),
):
    _require_can_write(current_user, "update customers")
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No hay ningun campo para actualizar")
    try:
        customer = await service.update_customer(current_user.organization_id, customer_id, changes)
    except ShipmentError as exc:
        raise _http_error(exc)
    return CustomerResponse(**serialize_customer(customer))


# ---------------------------------------------------------------------
# Envios: escritura
# ---------------------------------------------------------------------
@router.post("/", response_model=ShipmentDetailResponse, status_code=201)
async def create_shipment(
    data: ShipmentCreate,
    service: ShipmentService = Depends(get_shipment_service),
    current_user: User = Depends(get_current_user),
):
    _require_can_write(current_user, "create shipments")
    service_type = _parse_enum(ServiceType, data.service_type, "Servicio")
    try:
        shipment = await service.create_shipment(
            current_user.organization_id,
            customer_id=data.customer_id,
            origin_city=data.origin_city,
            destination_city=data.destination_city,
            service_type=service_type,
            tracking_number=data.tracking_number,
        )
    except ShipmentError as exc:
        raise _http_error(exc)
    return ShipmentDetailResponse(**serialize_shipment_detail(shipment))


@router.get("/{tracking_number}/status", response_model=ShipmentDetailResponse)
async def get_shipment_status(
    tracking_number: str,
    service: ShipmentService = Depends(get_shipment_service),
    current_user: User = Depends(get_current_user),
):
    shipment = await service.get_by_tracking_number(current_user.organization_id, tracking_number)
    if shipment is None:
        raise HTTPException(status_code=404, detail=f"No se encontro un envio con guia '{tracking_number}'")
    return ShipmentDetailResponse(**serialize_shipment_detail(shipment))


@router.get("/{tracking_number}/history", response_model=ShipmentHistoryResponse)
async def get_shipment_history(
    tracking_number: str,
    service: ShipmentService = Depends(get_shipment_service),
    current_user: User = Depends(get_current_user),
):
    """Linea de tiempo completa del envio (todos sus eventos, del mas
    antiguo al mas reciente)."""
    shipment = await service.get_by_tracking_number(current_user.organization_id, tracking_number)
    if shipment is None:
        raise HTTPException(status_code=404, detail=f"No se encontro un envio con guia '{tracking_number}'")
    return ShipmentHistoryResponse(
        tracking_number=shipment.tracking_number,
        status=shipment.status,
        customer_name=shipment.customer.full_name,
        origin_city=shipment.origin_city,
        destination_city=shipment.destination_city,
        events=[
            ShipmentEventResponse(
                event_type=e.event_type, city=e.city, notes=e.notes, occurred_at=e.occurred_at
            )
            for e in shipment.events
        ],
    )


@router.patch("/{tracking_number}/status", response_model=ShipmentDetailResponse)
async def update_shipment_status(
    tracking_number: str,
    data: ShipmentStatusUpdate,
    service: ShipmentService = Depends(get_shipment_service),
    current_user: User = Depends(get_current_user),
):
    # Actualizar un envio es operacion de negocio rutinaria, no gestion de
    # cuenta: solo se veta a VIEWER (ADMIN/OWNER no hace falta aca).
    _require_can_write(current_user, "update shipments")

    try:
        new_status = ShipmentStatus(data.new_status.lower())
    except ValueError:
        valid = ", ".join(s.value for s in ShipmentStatus)
        raise HTTPException(status_code=400, detail=f"Estado '{data.new_status}' invalido. Usa uno de: {valid}")

    shipment = await service.update_shipment_status(
        current_user.organization_id,
        tracking_number,
        new_status,
        city=data.city,
        notes=data.notes,
    )
    if shipment is None:
        raise HTTPException(status_code=404, detail=f"No se encontro un envio con guia '{tracking_number}'")
    return ShipmentDetailResponse(**serialize_shipment_detail(shipment))


@router.post("/{tracking_number}/notify", response_model=NotifyResponse)
async def notify_shipment_customer(
    tracking_number: str,
    data: NotifyRequest,
    service: ShipmentService = Depends(get_shipment_service),
    gmail: GmailConnectionService = Depends(get_gmail_service),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(get_current_user),
):
    """Envia un correo al cliente de ESTE envio desde el Gmail conectado.

    El destinatario no es un parametro: siempre es el correo que el
    cliente tiene registrado -- quien llama (una persona o el agente de
    chat) no puede usar esto para escribirle a una direccion arbitraria.
    Maximo 3 avisos por envio cada 24 horas, y cada uno queda guardado
    (quien, a quien, que dijo)."""
    _require_can_write(current_user, "notify customers")
    try:
        shipment = await service.get_shipment_or_raise(current_user.organization_id, tracking_number)
        to_email = await service.check_can_notify(shipment)
    except ShipmentError as exc:
        raise _http_error(exc)

    body = (
        f"{data.message.strip()}\n\n"
        f"Guia: {shipment.tracking_number}\n"
        f"-- {current_user.full_name}, {organization.name}"
    )
    try:
        await gmail.send_email(
            current_user.organization_id, to_email=to_email, subject=data.subject, body=body
        )
    except GmailNotConnectedError:
        raise _http_error(NotificationNotPossibleError("Gmail no esta conectado para esta organizacion"))
    except GmailError:
        logger.exception("Fallo el envio del aviso al cliente de la guia %s", tracking_number)
        raise HTTPException(status_code=502, detail="No se pudo enviar el correo (Gmail no respondio)")

    await service.record_notification(
        shipment, user_id=current_user.id, to_email=to_email, subject=data.subject, body=body
    )
    return NotifyResponse(
        tracking_number=shipment.tracking_number, sent_to=to_email, subject=data.subject
    )
