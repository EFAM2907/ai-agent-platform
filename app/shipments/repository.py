"""Acceso a datos de envios y clientes. La mayoria sigue siendo solo
lectura -- no existe (todavia) un flujo de negocio que cree envios o
clientes desde la propia app (el seed script inserta directo) -- pero
update_status/add_event si son de escritura: son el soporte de la tool
update_shipment_status (ver app.shipments.tools), el primer caso real
de una tool de escritura sobre datos de negocio de este dominio.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.shipments.models import Customer, Shipment, ShipmentEvent, ShipmentStatus


class ShipmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_shipments(
        self, organization_id: uuid.UUID, skip: int = 0, limit: int = 50
    ) -> list[Shipment]:
        # selectinload(Shipment.customer), no lazy load: en SQLAlchemy
        # async, acceder a una relacion no cargada fuera de un await
        # explicito revienta con MissingGreenlet -- y las tools
        # necesitan shipment.customer.full_name para cada fila.
        stmt = (
            select(Shipment)
            .options(selectinload(Shipment.customer))
            .where(
                Shipment.organization_id == organization_id,
                Shipment.deleted_at.is_(None),
            )
            .order_by(Shipment.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_shipments(self, organization_id: uuid.UUID) -> int:
        stmt = select(func.count()).where(
            Shipment.organization_id == organization_id,
            Shipment.deleted_at.is_(None),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_by_tracking_number(
        self, organization_id: uuid.UUID, tracking_number: str
    ) -> Shipment | None:
        stmt = (
            select(Shipment)
            .options(selectinload(Shipment.customer), selectinload(Shipment.events))
            .where(
                Shipment.organization_id == organization_id,
                Shipment.tracking_number == tracking_number,
                Shipment.deleted_at.is_(None),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_customers(
        self, organization_id: uuid.UUID, skip: int = 0, limit: int = 50
    ) -> list[Customer]:
        stmt = (
            select(Customer)
            .where(
                Customer.organization_id == organization_id,
                Customer.deleted_at.is_(None),
            )
            .order_by(Customer.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_customers(self, organization_id: uuid.UUID) -> int:
        stmt = select(func.count()).where(
            Customer.organization_id == organization_id,
            Customer.deleted_at.is_(None),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def update_status(self, shipment: Shipment, new_status: ShipmentStatus) -> Shipment:
        shipment.status = new_status
        await self.session.flush()
        return shipment

    async def add_event(
        self,
        shipment: Shipment,
        event_type: ShipmentStatus,
        *,
        city: str | None = None,
        notes: str | None = None,
        occurred_at: datetime | None = None,
    ) -> ShipmentEvent:
        # shipment.events.append(...), no ShipmentEvent(shipment_id=...)
        # + session.add(...): shipment ya viene con .events cargado via
        # selectinload (get_by_tracking_number) -- pasar por el lado del
        # relationship deja que el ORM setee el FK Y actualice esa
        # coleccion ya en memoria, para que quien llamo a esto pueda
        # seguir usando shipment.events[-1] sin volver a consultar.
        event = ShipmentEvent(
            organization_id=shipment.organization_id,
            event_type=event_type,
            city=city,
            notes=notes,
            occurred_at=occurred_at or datetime.now(timezone.utc),
        )
        shipment.events.append(event)
        await self.session.flush()
        return event
