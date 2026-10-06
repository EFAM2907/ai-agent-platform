"""Endpoints REST de envios/clientes. La tool de chat
update_shipment_status (servida hoy por el servidor MCP, ver
docs/roadmap.md seccion 2) reenvia aca en vez de llamar ShipmentService
directo -- este es el UNICO lugar que aplica las reglas de permisos:
lectura abierta a cualquier usuario autenticado del tenant
(envios/clientes son datos de negocio, no cuentas de la plataforma),
escritura (PATCH de estado) vetada solo para VIEWER."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.rate_limit import rate_limit
from app.shipments.models import ShipmentStatus
from app.shipments.repository import ShipmentRepository
from app.shipments.schemas import (
    CustomerListResponse,
    ShipmentDetailResponse,
    ShipmentListResponse,
    ShipmentStatusUpdate,
    serialize_customer,
    serialize_shipment_detail,
    serialize_shipment_summary,
)
from app.shipments.service import ShipmentService
from app.users.models import User, UserRole


def get_shipment_service(session: AsyncSession = Depends(get_db)) -> ShipmentService:
    return ShipmentService(ShipmentRepository(session), session)


router = APIRouter(prefix="/shipments", tags=["shipments"], dependencies=[Depends(rate_limit)])


@router.get("/", response_model=ShipmentListResponse)
async def list_shipments(
    skip: int = 0,
    limit: int = Query(default=50, le=100),
    service: ShipmentService = Depends(get_shipment_service),
    current_user: User = Depends(get_current_user),
):
    shipments, total = await service.list_shipments(current_user.organization_id, skip, limit)
    return ShipmentListResponse(
        total_shipments=total,
        returned=len(shipments),
        shipments=[serialize_shipment_summary(s) for s in shipments],
    )


@router.get("/customers", response_model=CustomerListResponse)
async def list_customers(
    skip: int = 0,
    limit: int = Query(default=50, le=100),
    service: ShipmentService = Depends(get_shipment_service),
    current_user: User = Depends(get_current_user),
):
    # Declarado antes de /{tracking_number}/status a proposito, mismo
    # motivo que GET /users/me antes de GET /users/{user_id}: aunque
    # hoy no colisionan (un tracking_number sin /status detras no tiene
    # ruta), mantenerlo arriba evita sorpresas si alguna vez se agrega
    # un GET /{tracking_number} sin sufijo.
    customers, total = await service.list_customers(current_user.organization_id, skip, limit)
    return CustomerListResponse(
        total_customers=total,
        returned=len(customers),
        customers=[serialize_customer(c) for c in customers],
    )


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


@router.patch("/{tracking_number}/status", response_model=ShipmentDetailResponse)
async def update_shipment_status(
    tracking_number: str,
    data: ShipmentStatusUpdate,
    service: ShipmentService = Depends(get_shipment_service),
    current_user: User = Depends(get_current_user),
):
    # Ver el docstring del modulo para el porque de vetar solo VIEWER
    # (actualizar un envio es operacion de negocio rutinaria, no
    # gestion de cuenta, asi que ADMIN/OWNER no hace falta aca).
    if current_user.role == UserRole.VIEWER:
        raise HTTPException(status_code=403, detail="Viewer accounts cannot update shipments, only view them")

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
