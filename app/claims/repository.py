from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.claims.models import ACTIVE_STATUSES, Claim, ClaimEvent
from app.shipments.models import Shipment


class ClaimRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def count_for_year(self, organization_id: uuid.UUID, year: int) -> int:
        """Reclamos del tenant creados en `year` (incluye cerrados): base del
        consecutivo CLM-AAAA-NNNNNN."""
        start = datetime(year, 1, 1, tzinfo=timezone.utc)
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        stmt = select(func.count(Claim.id)).where(
            Claim.organization_id == organization_id,
            Claim.created_at >= start,
            Claim.created_at < end,
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def active_for_shipment(
        self, organization_id: uuid.UUID, shipment_id: uuid.UUID, claim_type: str
    ) -> Claim | None:
        stmt = select(Claim).where(
            Claim.organization_id == organization_id,
            Claim.shipment_id == shipment_id,
            Claim.claim_type == claim_type,
            Claim.status.in_([s.value for s in ACTIVE_STATUSES]),
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def add(self, claim: Claim) -> Claim:
        self.session.add(claim)
        await self.session.flush()
        return claim

    async def add_event(self, event: ClaimEvent) -> ClaimEvent:
        self.session.add(event)
        await self.session.flush()
        return event

    async def get_by_number(
        self, organization_id: uuid.UUID, claim_number: str, *, refresh: bool = False
    ) -> Claim | None:
        """refresh=True vuelve a leer la fila y su historial aunque el
        objeto ya este en la sesion (populate_existing) -- se usa despues
        de escribir, para devolver el estado real sin expirar el resto de
        objetos de la sesion (como el usuario autenticado)."""
        stmt = (
            select(Claim)
            .options(selectinload(Claim.events))
            .where(Claim.organization_id == organization_id, Claim.claim_number == claim_number)
        )
        if refresh:
            stmt = stmt.execution_options(populate_existing=True)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_claims(
        self,
        organization_id: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 50,
        status: str | None = None,
        shipment_id: uuid.UUID | None = None,
    ) -> tuple[list[Claim], int]:
        conditions = [Claim.organization_id == organization_id]
        if status:
            conditions.append(Claim.status == status)
        if shipment_id:
            conditions.append(Claim.shipment_id == shipment_id)
        stmt = (
            select(Claim)
            .options(selectinload(Claim.events))
            .where(*conditions)
            .order_by(Claim.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        total = (await self.session.execute(select(func.count(Claim.id)).where(*conditions))).scalar_one()
        return rows, total

    async def shipments_by_id(
        self, organization_id: uuid.UUID, shipment_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, Shipment]:
        """Envios (con cliente) de una lista de reclamos, en una sola consulta."""
        if not shipment_ids:
            return {}
        stmt = (
            select(Shipment)
            .options(selectinload(Shipment.customer))
            .where(Shipment.organization_id == organization_id, Shipment.id.in_(shipment_ids))
        )
        return {s.id: s for s in (await self.session.execute(stmt)).scalars().all()}
