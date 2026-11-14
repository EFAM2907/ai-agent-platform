"""Acceso a datos de envios y clientes: lectura con filtros, alta y
edicion de clientes, alta de envios, historial, notificaciones y los
agregados de estadisticas. El cambio de estado (update_status /
add_event) es lo que usa PATCH /shipments/{tracking_number}/status,
que la tool de chat update_shipment_status llama via el servidor MCP.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.shipments.models import (
    Customer,
    ServiceType,
    Shipment,
    ShipmentEvent,
    ShipmentNotification,
    ShipmentStatus,
)


@dataclass
class ShipmentFilters:
    status: ShipmentStatus | None = None
    service_type: ServiceType | None = None
    customer_id: uuid.UUID | None = None
    city: str | None = None  # coincide con origen O destino
    query: str | None = None  # guia (prefijo) o nombre del cliente


def _ilike(column: Any, pattern: str):
    """ILIKE insensible a mayusculas Y a tildes: 'julian' encuentra a
    'Julián' (extension unaccent, migracion b8e1f4a7c0d2)."""
    return func.unaccent(column).ilike(func.unaccent(pattern), escape="\\")


def _like(value: str) -> str:
    """Patron ILIKE seguro: el texto del usuario no puede aportar comodines."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped.strip()}%"


class ShipmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Envios
    # ------------------------------------------------------------------
    @staticmethod
    def _shipment_conditions(organization_id: uuid.UUID, filters: ShipmentFilters | None) -> list:
        conditions = [Shipment.organization_id == organization_id, Shipment.deleted_at.is_(None)]
        if filters is None:
            return conditions
        if filters.status:
            conditions.append(Shipment.status == filters.status)
        if filters.service_type:
            conditions.append(Shipment.service_type == filters.service_type)
        if filters.customer_id:
            conditions.append(Shipment.customer_id == filters.customer_id)
        if filters.city:
            pattern = _like(filters.city)
            conditions.append(
                or_(_ilike(Shipment.origin_city, pattern), _ilike(Shipment.destination_city, pattern))
            )
        if filters.query:
            pattern = _like(filters.query)
            conditions.append(
                or_(
                    _ilike(Shipment.tracking_number, pattern),
                    Shipment.customer_id.in_(
                        select(Customer.id).where(
                            Customer.organization_id == organization_id,
                            _ilike(Customer.full_name, pattern),
                        )
                    ),
                )
            )
        return conditions

    async def list_shipments(
        self,
        organization_id: uuid.UUID,
        skip: int = 0,
        limit: int = 50,
        filters: ShipmentFilters | None = None,
    ) -> list[Shipment]:
        # selectinload(Shipment.customer), no lazy load: en SQLAlchemy
        # async, acceder a una relacion no cargada fuera de un await
        # explicito revienta con MissingGreenlet -- y las tools
        # necesitan shipment.customer.full_name para cada fila.
        stmt = (
            select(Shipment)
            .options(selectinload(Shipment.customer))
            .where(*self._shipment_conditions(organization_id, filters))
            .order_by(Shipment.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_shipments(
        self, organization_id: uuid.UUID, filters: ShipmentFilters | None = None
    ) -> int:
        stmt = select(func.count(Shipment.id)).where(
            *self._shipment_conditions(organization_id, filters)
        )
        return (await self.session.execute(stmt)).scalar_one()

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

    async def tracking_number_exists(self, organization_id: uuid.UUID, tracking_number: str) -> bool:
        stmt = select(Shipment.id).where(
            Shipment.organization_id == organization_id,
            Shipment.tracking_number == tracking_number,
            Shipment.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).first() is not None

    async def create_shipment(
        self,
        organization_id: uuid.UUID,
        customer_id: uuid.UUID,
        tracking_number: str,
        service_type: ServiceType,
        origin_city: str,
        destination_city: str,
    ) -> Shipment:
        shipment = Shipment(
            organization_id=organization_id,
            customer_id=customer_id,
            tracking_number=tracking_number,
            status=ShipmentStatus.ADMITIDO,
            service_type=service_type,
            origin_city=origin_city,
            destination_city=destination_city,
        )
        # events=[]: inicializa la coleccion para poder agregar el evento
        # de admision sin disparar una carga perezosa (MissingGreenlet).
        shipment.events = []
        self.session.add(shipment)
        await self.session.flush()
        return shipment

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

    async def list_pending_with_events(
        self, organization_id: uuid.UUID, idle_for: timedelta, limit: int = 500
    ) -> list[Shipment]:
        """Envios NO entregados cuyo ultimo evento (o su creacion, si no
        tienen) es mas viejo que `idle_for`, con cliente y eventos
        cargados -- el prefiltro barato en SQL antes de aplicar la
        politica de demoras (app.shipments.delays) en Python."""
        cutoff = datetime.now(timezone.utc) - idle_for
        last_event = (
            select(func.max(ShipmentEvent.occurred_at))
            .where(ShipmentEvent.shipment_id == Shipment.id)
            .correlate(Shipment)
            .scalar_subquery()
        )
        stmt = (
            select(Shipment)
            .options(selectinload(Shipment.customer), selectinload(Shipment.events))
            .where(
                Shipment.organization_id == organization_id,
                Shipment.deleted_at.is_(None),
                Shipment.status != ShipmentStatus.ENTREGADO,
                func.coalesce(last_event, Shipment.created_at) < cutoff,
            )
            .order_by(func.coalesce(last_event, Shipment.created_at))
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def counts_by(self, organization_id: uuid.UUID, column: Any) -> dict[str, int]:
        stmt = (
            select(column, func.count(Shipment.id))
            .where(Shipment.organization_id == organization_id, Shipment.deleted_at.is_(None))
            .group_by(column)
        )
        return {
            (key.value if hasattr(key, "value") else str(key)): count
            for key, count in (await self.session.execute(stmt)).all()
        }

    # ------------------------------------------------------------------
    # Clientes
    # ------------------------------------------------------------------
    async def list_customers(
        self,
        organization_id: uuid.UUID,
        skip: int = 0,
        limit: int = 50,
        search: str | None = None,
    ) -> list[Customer]:
        stmt = (
            select(Customer)
            .where(*self._customer_conditions(organization_id, search))
            .order_by(Customer.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_customers(self, organization_id: uuid.UUID, search: str | None = None) -> int:
        stmt = select(func.count(Customer.id)).where(
            *self._customer_conditions(organization_id, search)
        )
        return (await self.session.execute(stmt)).scalar_one()

    @staticmethod
    def _customer_conditions(organization_id: uuid.UUID, search: str | None) -> list:
        conditions = [Customer.organization_id == organization_id, Customer.deleted_at.is_(None)]
        if search and search.strip():
            pattern = _like(search)
            conditions.append(
                or_(
                    _ilike(Customer.full_name, pattern),
                    _ilike(Customer.email, pattern),
                    _ilike(Customer.document_id, pattern),
                    _ilike(Customer.phone, pattern),
                )
            )
        return conditions

    async def get_customer(self, organization_id: uuid.UUID, customer_id: uuid.UUID) -> Customer | None:
        stmt = select(Customer).where(
            Customer.organization_id == organization_id,
            Customer.id == customer_id,
            Customer.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def find_customer_conflict(
        self,
        organization_id: uuid.UUID,
        *,
        email: str | None,
        document_id: str | None,
        exclude_id: uuid.UUID | None = None,
    ) -> Customer | None:
        """Otro cliente activo del tenant con el mismo correo o documento."""
        identity = []
        if email:
            identity.append(func.lower(Customer.email) == email.lower())
        if document_id:
            identity.append(Customer.document_id == document_id)
        if not identity:
            return None
        conditions = [
            Customer.organization_id == organization_id,
            Customer.deleted_at.is_(None),
            or_(*identity),
        ]
        if exclude_id:
            conditions.append(Customer.id != exclude_id)
        return (await self.session.execute(select(Customer).where(*conditions))).scalars().first()

    async def create_customer(self, organization_id: uuid.UUID, **fields: Any) -> Customer:
        customer = Customer(organization_id=organization_id, **fields)
        self.session.add(customer)
        await self.session.flush()
        return customer

    async def update_customer(self, customer: Customer, **fields: Any) -> Customer:
        for name, value in fields.items():
            setattr(customer, name, value)
        await self.session.flush()
        return customer

    async def customer_shipments(
        self, organization_id: uuid.UUID, customer_id: uuid.UUID, limit: int = 20
    ) -> list[Shipment]:
        stmt = (
            select(Shipment)
            .options(selectinload(Shipment.customer))
            .where(
                Shipment.organization_id == organization_id,
                Shipment.customer_id == customer_id,
                Shipment.deleted_at.is_(None),
            )
            .order_by(Shipment.created_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # Notificaciones al cliente
    # ------------------------------------------------------------------
    async def count_notifications_since(self, shipment_id: uuid.UUID, since: datetime) -> int:
        stmt = select(func.count(ShipmentNotification.id)).where(
            ShipmentNotification.shipment_id == shipment_id,
            ShipmentNotification.created_at >= since,
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def add_notification(self, notification: ShipmentNotification) -> ShipmentNotification:
        self.session.add(notification)
        await self.session.flush()
        return notification
