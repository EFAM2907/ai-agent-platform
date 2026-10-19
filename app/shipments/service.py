from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.shipments.delays import (
    LEVEL_DELAYED,
    LEVEL_EXTREME,
    LEVEL_OK,
    LEVEL_PREVENTIVE,
    assess_delay,
    is_hard_to_reach,
    normalize_city,
)
from app.shipments.exceptions import (
    CustomerConflictError,
    CustomerNotFoundError,
    InvalidShipmentError,
    NotificationLimitError,
    ShipmentNotFoundError,
)
from app.shipments.models import (
    Customer,
    ServiceType,
    Shipment,
    ShipmentNotification,
    ShipmentStatus,
)
from app.shipments.repository import ShipmentFilters, ShipmentRepository

_TRACKING_PATTERN = re.compile(r"^[A-Za-z0-9-]{4,30}$")
_GENERATED_TRACKING_DIGITS = 8
MAX_NOTIFICATIONS_PER_SHIPMENT_PER_DAY = 3
# Piso del prefiltro SQL de demoras: el umbral "preventivo" mas bajo de la
# politica es 2 dias habiles, que son al menos 2 dias de calendario.
_DELAY_PREFILTER = timedelta(days=2)


class ShipmentService:
    def __init__(self, repository: ShipmentRepository, session: AsyncSession) -> None:
        self.repository = repository
        self.session = session

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------
    async def list_shipments(
        self,
        organization_id: uuid.UUID,
        skip: int = 0,
        limit: int = 50,
        filters: ShipmentFilters | None = None,
    ) -> tuple[list[Shipment], int]:
        """Devuelve la pagina pedida junto con el total real de envios
        activos del tenant que cumplen los filtros -- asi "cuantos envios
        tenemos" se puede responder con el total exacto aunque limit sea
        mas chico que el dataset completo."""
        shipments = await self.repository.list_shipments(organization_id, skip, limit, filters)
        total = await self.repository.count_shipments(organization_id, filters)
        return shipments, total

    async def get_by_tracking_number(
        self, organization_id: uuid.UUID, tracking_number: str
    ) -> Shipment | None:
        return await self.repository.get_by_tracking_number(organization_id, tracking_number)

    async def list_customers(
        self, organization_id: uuid.UUID, skip: int = 0, limit: int = 50, search: str | None = None
    ) -> tuple[list[Customer], int]:
        customers = await self.repository.list_customers(organization_id, skip, limit, search)
        total = await self.repository.count_customers(organization_id, search)
        return customers, total

    async def get_customer(self, organization_id: uuid.UUID, customer_id: uuid.UUID) -> Customer:
        customer = await self.repository.get_customer(organization_id, customer_id)
        if customer is None:
            raise CustomerNotFoundError("Cliente no encontrado")
        return customer

    # ------------------------------------------------------------------
    # Clientes
    # ------------------------------------------------------------------
    async def create_customer(
        self,
        organization_id: uuid.UUID,
        *,
        full_name: str,
        email: str | None = None,
        phone: str | None = None,
        document_id: str | None = None,
    ) -> Customer:
        """Un correo o documento ya registrado en el tenant es un 409 que
        nombra al cliente existente: el correo del cliente es lo que
        autoriza a ver sus envios por Gmail (ver app.gmail.processor), asi
        que dos clientes con el mismo correo mezclarian sus datos."""
        email = email.strip().lower() if email else None
        document_id = document_id.strip() if document_id else None
        existing = await self.repository.find_customer_conflict(
            organization_id, email=email, document_id=document_id
        )
        if existing is not None:
            raise CustomerConflictError(
                f"Ya existe un cliente con ese correo o documento: {existing.full_name} (id {existing.id})"
            )
        customer = await self.repository.create_customer(
            organization_id,
            full_name=full_name.strip(),
            email=email,
            phone=phone.strip() if phone else None,
            document_id=document_id,
        )
        await self.session.commit()
        return customer

    async def update_customer(
        self, organization_id: uuid.UUID, customer_id: uuid.UUID, changes: dict[str, Any]
    ) -> Customer:
        customer = await self.get_customer(organization_id, customer_id)
        clean: dict[str, Any] = {}
        for name, value in changes.items():
            if isinstance(value, str):
                value = value.strip()
            if name == "email" and value:
                value = value.lower()
            clean[name] = value or None if name != "full_name" else value
        if "full_name" in clean and not clean["full_name"]:
            raise InvalidShipmentError("El nombre del cliente no puede quedar vacio")
        existing = await self.repository.find_customer_conflict(
            organization_id,
            email=clean.get("email"),
            document_id=clean.get("document_id"),
            exclude_id=customer.id,
        )
        if existing is not None:
            raise CustomerConflictError(
                f"Otro cliente ya usa ese correo o documento: {existing.full_name} (id {existing.id})"
            )
        await self.repository.update_customer(customer, **clean)
        await self.session.commit()
        return customer

    # ------------------------------------------------------------------
    # Envios
    # ------------------------------------------------------------------
    async def create_shipment(
        self,
        organization_id: uuid.UUID,
        *,
        customer_id: uuid.UUID,
        origin_city: str,
        destination_city: str,
        service_type: ServiceType,
        tracking_number: str | None = None,
    ) -> Shipment:
        """Admite un envio nuevo con su primer evento (ADMITIDO en la
        ciudad de origen), en una sola transaccion. Aplica las reglas de
        servicio de la KB: Same Day solo dentro de la misma ciudad, y
        Express no esta disponible hacia zonas rurales o de dificil
        acceso."""
        customer = await self.repository.get_customer(organization_id, customer_id)
        if customer is None:
            raise CustomerNotFoundError("Cliente no encontrado")

        origin, destination = origin_city.strip(), destination_city.strip()
        if not origin or not destination:
            raise InvalidShipmentError("Origen y destino son obligatorios")
        same_city = normalize_city(origin) == normalize_city(destination)
        if service_type == ServiceType.SAME_DAY and not same_city:
            raise InvalidShipmentError(
                "Same Day solo esta disponible dentro de la misma ciudad (origen y destino distintos)"
            )
        if service_type == ServiceType.EXPRESS and is_hard_to_reach(destination):
            raise InvalidShipmentError(
                f"Express no esta disponible hacia {destination} (zona rural o de dificil acceso); usa el servicio estandar"
            )

        number = await self._resolve_tracking_number(organization_id, tracking_number)
        shipment = await self.repository.create_shipment(
            organization_id, customer.id, number, service_type, origin, destination
        )
        await self.repository.add_event(
            shipment, ShipmentStatus.ADMITIDO, city=origin, notes="Envio admitido"
        )
        await self.session.commit()
        # Se devuelve con el cliente cargado, como el resto de lecturas.
        shipment.customer = customer
        return shipment

    async def _resolve_tracking_number(
        self, organization_id: uuid.UUID, requested: str | None
    ) -> str:
        if requested:
            number = requested.strip()
            if not _TRACKING_PATTERN.match(number):
                raise InvalidShipmentError(
                    "La guia debe tener entre 4 y 30 caracteres (letras, numeros o guiones)"
                )
            if await self.repository.tracking_number_exists(organization_id, number):
                raise CustomerConflictError(f"Ya existe un envio con la guia '{number}'")
            return number
        # Numerica de 8 digitos: encaja en el formato de guia "suelta" que
        # reconoce el procesador de correos (settings.gmail_tracking_bare_pattern).
        for _ in range(10):
            number = "".join(str(secrets.randbelow(10)) for _ in range(_GENERATED_TRACKING_DIGITS))
            if number[0] != "0" and not await self.repository.tracking_number_exists(
                organization_id, number
            ):
                return number
        raise RuntimeError("No se pudo generar una guia unica")

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

    # ------------------------------------------------------------------
    # Demoras y estadisticas
    # ------------------------------------------------------------------
    async def delayed_shipments(
        self, organization_id: uuid.UUID, *, minimum_level: str = LEVEL_DELAYED, limit: int = 100
    ) -> list[tuple[Shipment, dict]]:
        """Envios que la politica de la KB considera demorados (o desde
        'preventive'), del mas atrasado al menos, con la evaluacion que
        lo justifica (zona, dias habiles sin evento, umbrales)."""
        order = [LEVEL_OK, LEVEL_PREVENTIVE, LEVEL_DELAYED, LEVEL_EXTREME]
        floor = order.index(minimum_level)
        candidates = await self.repository.list_pending_with_events(
            organization_id, _DELAY_PREFILTER
        )
        now = datetime.now(timezone.utc)
        assessed = []
        for shipment in candidates:
            assessment = assess_delay(shipment, now)
            if order.index(assessment.level) >= floor:
                assessed.append((shipment, assessment.as_dict()))
        assessed.sort(key=lambda pair: pair[1]["business_days_without_event"], reverse=True)
        return assessed[:limit]

    async def stats(self, organization_id: uuid.UUID) -> dict[str, Any]:
        by_status = await self.repository.counts_by(organization_id, Shipment.status)
        by_service = await self.repository.counts_by(organization_id, Shipment.service_type)
        delayed = await self.delayed_shipments(
            organization_id, minimum_level=LEVEL_PREVENTIVE, limit=500
        )
        levels = {LEVEL_PREVENTIVE: 0, LEVEL_DELAYED: 0, LEVEL_EXTREME: 0}
        for _shipment, assessment in delayed:
            levels[assessment["level"]] += 1
        return {
            "total_shipments": sum(by_status.values()),
            "total_customers": await self.repository.count_customers(organization_id),
            "by_status": by_status,
            "by_service": by_service,
            "attention": {
                "preventive_follow_up": levels[LEVEL_PREVENTIVE],
                "delayed": levels[LEVEL_DELAYED],
                "extreme_delay": levels[LEVEL_EXTREME],
            },
        }

    # ------------------------------------------------------------------
    # Notificar al cliente
    # ------------------------------------------------------------------
    async def check_can_notify(self, shipment: Shipment) -> str:
        """Valida que se pueda avisar al cliente y devuelve su correo."""
        from app.shipments.exceptions import NotificationNotPossibleError

        email = (shipment.customer.email or "").strip()
        if not email:
            raise NotificationNotPossibleError(
                f"El cliente {shipment.customer.full_name} no tiene correo registrado"
            )
        since = datetime.now(timezone.utc) - timedelta(days=1)
        sent = await self.repository.count_notifications_since(shipment.id, since)
        if sent >= MAX_NOTIFICATIONS_PER_SHIPMENT_PER_DAY:
            raise NotificationLimitError(
                f"Ya se enviaron {sent} avisos sobre la guia {shipment.tracking_number} en las "
                "ultimas 24 horas (maximo permitido); intenta manana o contacta al cliente por otro medio"
            )
        return email

    async def record_notification(
        self,
        shipment: Shipment,
        *,
        user_id: uuid.UUID,
        to_email: str,
        subject: str,
        body: str,
    ) -> None:
        await self.repository.add_notification(
            ShipmentNotification(
                organization_id=shipment.organization_id,
                shipment_id=shipment.id,
                sent_by_user_id=user_id,
                to_email=to_email,
                subject=subject,
                body=body,
            )
        )
        await self.session.commit()

    async def get_shipment_or_raise(
        self, organization_id: uuid.UUID, tracking_number: str
    ) -> Shipment:
        shipment = await self.repository.get_by_tracking_number(organization_id, tracking_number)
        if shipment is None:
            raise ShipmentNotFoundError(f"No se encontro un envio con guia '{tracking_number}'")
        return shipment
