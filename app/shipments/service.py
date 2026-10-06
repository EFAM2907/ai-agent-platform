from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.shipments.models import Customer, Shipment, ShipmentStatus
from app.shipments.repository import ShipmentRepository


class ShipmentService:
    def __init__(self, repository: ShipmentRepository, session: AsyncSession) -> None:
        self.repository = repository
        self.session = session

    async def list_shipments(
        self, organization_id: uuid.UUID, skip: int = 0, limit: int = 50
    ) -> tuple[list[Shipment], int]:
        """Devuelve la pagina pedida junto con el total real de envios
        activos del tenant -- asi "cuantos envios tenemos" se puede
        responder con el total exacto aunque limit sea mas chico que
        el dataset completo."""
        shipments = await self.repository.list_shipments(organization_id, skip, limit)
        total = await self.repository.count_shipments(organization_id)
        return shipments, total

    async def get_by_tracking_number(
        self, organization_id: uuid.UUID, tracking_number: str
    ) -> Shipment | None:
        return await self.repository.get_by_tracking_number(organization_id, tracking_number)

    async def list_customers(
        self, organization_id: uuid.UUID, skip: int = 0, limit: int = 50
    ) -> tuple[list[Customer], int]:
        customers = await self.repository.list_customers(organization_id, skip, limit)
        total = await self.repository.count_customers(organization_id)
        return customers, total

    async def update_shipment_status(
        self,
        organization_id: uuid.UUID,
        tracking_number: str,
        new_status: ShipmentStatus,
        *,
        city: str | None = None,
        notes: str | None = None,
    ) -> Shipment | None:
        """Actualiza Shipment.status Y agrega el ShipmentEvent
        correspondiente en la misma transaccion -- nunca uno sin el
        otro, para no romper la garantia que ya documenta
        Shipment.events: que shipment_events es la fuente de verdad
        del historial completo, no una copia que se pueda desincronizar
        del estado actual."""
        shipment = await self.repository.get_by_tracking_number(organization_id, tracking_number)
        if shipment is None:
            return None
        await self.repository.update_status(shipment, new_status)
        await self.repository.add_event(shipment, new_status, city=city, notes=notes)
        await self.session.commit()
        return shipment
