"""Reclamos (investigaciones sobre un envio).

Permisos: leer, cualquier usuario autenticado de la organizacion; abrir y
cambiar de estado, cualquier rol salvo VIEWER (mismo criterio que
actualizar un envio: es operacion de negocio rutinaria, no gestion de
cuentas). La tool de chat open_claim / update_claim_status reenvia aca
via el servidor MCP.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.claims.models import REC_CODES, Claim, ClaimStatus, ClaimType
from app.claims.repository import ClaimRepository
from app.claims.schemas import (
    ClaimEventResponse,
    ClaimListResponse,
    ClaimOpen,
    ClaimResponse,
    ClaimStatusUpdate,
)
from app.claims.service import ClaimError, ClaimService, parse_claim_status
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.rate_limit import rate_limit
from app.shipments.models import Shipment
from app.shipments.repository import ShipmentRepository
from app.users.models import User, UserRole

router = APIRouter(prefix="/claims", tags=["claims"], dependencies=[Depends(rate_limit)])


def get_claim_service(session: AsyncSession = Depends(get_db)) -> ClaimService:
    return ClaimService(ClaimRepository(session), ShipmentRepository(session), session)


def _require_can_write(user: User) -> None:
    if user.role == UserRole.VIEWER:
        raise HTTPException(status_code=403, detail="Viewer accounts cannot open or update claims")


def _serialize(claim: Claim, shipment: Shipment) -> ClaimResponse:
    now = datetime.now(timezone.utc)
    still_open = claim.status in (ClaimStatus.ABIERTO.value, ClaimStatus.EN_INVESTIGACION.value)
    return ClaimResponse(
        claim_number=claim.claim_number,
        tracking_number=shipment.tracking_number,
        customer_name=shipment.customer.full_name,
        claim_type=claim.claim_type,
        rec_code=REC_CODES[ClaimType(claim.claim_type)],
        status=claim.status,
        description=claim.description,
        resolution=claim.resolution,
        sla_due_at=claim.sla_due_at,
        # Solo "vencido" mientras sigue vivo: uno ya resuelto no se cuenta.
        sla_overdue=still_open and claim.sla_due_at < now,
        opened_by_user_id=claim.opened_by_user_id,
        created_at=claim.created_at,
        resolved_at=claim.resolved_at,
        closed_at=claim.closed_at,
        history=[
            ClaimEventResponse(
                status=event.status, note=event.note, user_id=event.user_id, created_at=event.created_at
            )
            for event in claim.events
        ],
    )


def _http_error(exc: ClaimError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.post("/", response_model=ClaimResponse, status_code=201)
async def open_claim(
    data: ClaimOpen,
    current_user: User = Depends(get_current_user),
    service: ClaimService = Depends(get_claim_service),
) -> ClaimResponse:
    _require_can_write(current_user)
    try:
        claim = await service.open_claim(
            current_user.organization_id,
            current_user.id,
            data.tracking_number,
            data.claim_type,
            data.description,
        )
    except ClaimError as exc:
        raise _http_error(exc)
    shipment = (await service.repository.shipments_by_id(current_user.organization_id, {claim.shipment_id}))[
        claim.shipment_id
    ]
    return _serialize(claim, shipment)


@router.get("/", response_model=ClaimListResponse)
async def list_claims(
    skip: int = 0,
    limit: int = Query(default=50, le=100),
    status: str | None = None,
    tracking_number: str | None = None,
    current_user: User = Depends(get_current_user),
    service: ClaimService = Depends(get_claim_service),
) -> ClaimListResponse:
    status_value = None
    if status:
        try:
            status_value = parse_claim_status(status).value
        except ClaimError as exc:
            raise _http_error(exc)

    shipment_id = None
    if tracking_number:
        shipment = await service.shipment_repository.get_by_tracking_number(
            current_user.organization_id, tracking_number
        )
        if shipment is None:
            return ClaimListResponse(total=0, returned=0, claims=[])
        shipment_id = shipment.id

    rows, total = await service.repository.list_claims(
        current_user.organization_id, skip=skip, limit=limit, status=status_value, shipment_id=shipment_id
    )
    shipments = await service.repository.shipments_by_id(
        current_user.organization_id, {c.shipment_id for c in rows}
    )
    return ClaimListResponse(
        total=total,
        returned=len(rows),
        claims=[_serialize(c, shipments[c.shipment_id]) for c in rows],
    )


@router.get("/{claim_number}", response_model=ClaimResponse)
async def get_claim(
    claim_number: str,
    current_user: User = Depends(get_current_user),
    service: ClaimService = Depends(get_claim_service),
) -> ClaimResponse:
    claim = await service.repository.get_by_number(current_user.organization_id, claim_number)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"No se encontro el reclamo '{claim_number}'")
    shipment = (await service.repository.shipments_by_id(current_user.organization_id, {claim.shipment_id}))[
        claim.shipment_id
    ]
    return _serialize(claim, shipment)


@router.patch("/{claim_number}/status", response_model=ClaimResponse)
async def update_claim_status(
    claim_number: str,
    data: ClaimStatusUpdate,
    current_user: User = Depends(get_current_user),
    service: ClaimService = Depends(get_claim_service),
) -> ClaimResponse:
    _require_can_write(current_user)
    try:
        claim = await service.update_status(
            current_user.organization_id, current_user.id, claim_number, data.new_status, data.note
        )
    except ClaimError as exc:
        raise _http_error(exc)
    shipment = (await service.repository.shipments_by_id(current_user.organization_id, {claim.shipment_id}))[
        claim.shipment_id
    ]
    return _serialize(claim, shipment)
