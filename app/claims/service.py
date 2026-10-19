from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.claims.models import (
    SLA_HOURS,
    TRANSITIONS,
    Claim,
    ClaimEvent,
    ClaimStatus,
    ClaimType,
)
from app.claims.repository import ClaimRepository
from app.core.business_days import add_business_days
from app.shipments.repository import ShipmentRepository

_NUMBER_RETRIES = 3


class ClaimError(Exception):
    """Base de los errores de negocio de reclamos; `status_code` es el
    HTTP que corresponde (la API solo traduce, no decide)."""

    status_code = 400


class ShipmentNotFoundError(ClaimError):
    status_code = 404


class ClaimNotFoundError(ClaimError):
    status_code = 404


class InvalidClaimTypeError(ClaimError):
    status_code = 400


class InvalidTransitionError(ClaimError):
    status_code = 400


class DuplicateClaimError(ClaimError):
    status_code = 409


def parse_claim_type(value: str) -> ClaimType:
    try:
        return ClaimType(value.strip().lower())
    except ValueError:
        valid = ", ".join(t.value for t in ClaimType)
        raise InvalidClaimTypeError(f"Tipo de reclamo '{value}' invalido. Usa uno de: {valid}")


def parse_claim_status(value: str) -> ClaimStatus:
    try:
        return ClaimStatus(value.strip().lower())
    except ValueError:
        valid = ", ".join(s.value for s in ClaimStatus)
        raise InvalidTransitionError(f"Estado '{value}' invalido. Usa uno de: {valid}")


class ClaimService:
    def __init__(
        self,
        repository: ClaimRepository,
        shipment_repository: ShipmentRepository,
        session: AsyncSession,
    ) -> None:
        self.repository = repository
        self.shipment_repository = shipment_repository
        self.session = session

    async def open_claim(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        tracking_number: str,
        claim_type: str,
        description: str,
    ) -> Claim:
        """Abre un reclamo sobre un envio. Un envio no puede tener dos
        reclamos ACTIVOS del mismo tipo: un segundo intento (el cliente
        insiste, el agente reintenta) devuelve el numero del existente en
        vez de crear un duplicado que partiria el seguimiento en dos."""
        kind = parse_claim_type(claim_type)
        shipment = await self.shipment_repository.get_by_tracking_number(
            organization_id, tracking_number
        )
        if shipment is None:
            raise ShipmentNotFoundError(f"No se encontro un envio con guia '{tracking_number}'")

        existing = await self.repository.active_for_shipment(organization_id, shipment.id, kind.value)
        if existing is not None:
            raise DuplicateClaimError(
                f"Ya existe un reclamo activo de tipo '{kind.value}' para la guia "
                f"{tracking_number}: {existing.claim_number} (estado {existing.status})"
            )

        now = datetime.now(timezone.utc)
        sla_due_at = add_business_days(now, math.ceil(SLA_HOURS[kind] / 24))

        for attempt in range(_NUMBER_RETRIES):
            number = await self._next_number(organization_id, now.year, attempt)
            claim = Claim(
                organization_id=organization_id,
                shipment_id=shipment.id,
                claim_number=number,
                claim_type=kind.value,
                status=ClaimStatus.ABIERTO.value,
                description=description.strip(),
                opened_by_user_id=user_id,
                sla_due_at=sla_due_at,
            )
            try:
                # SAVEPOINT: si dos reclamos simultaneos eligen el mismo
                # consecutivo, el indice unico rechaza al segundo y se
                # reintenta con el siguiente sin perder la transaccion.
                async with self.session.begin_nested():
                    await self.repository.add(claim)
                break
            except IntegrityError:
                if attempt == _NUMBER_RETRIES - 1:
                    raise
        else:  # pragma: no cover -- el for siempre hace break o relanza
            raise RuntimeError("No se pudo asignar un numero de reclamo")

        await self.repository.add_event(
            ClaimEvent(
                claim_id=claim.id,
                organization_id=organization_id,
                status=ClaimStatus.ABIERTO.value,
                note=description.strip(),
                user_id=user_id,
            )
        )
        await self.session.commit()
        return await self._reload(organization_id, claim.claim_number)

    async def update_status(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        claim_number: str,
        new_status: str,
        note: str | None,
    ) -> Claim:
        claim = await self.repository.get_by_number(organization_id, claim_number)
        if claim is None:
            raise ClaimNotFoundError(f"No se encontro el reclamo '{claim_number}'")

        target = parse_claim_status(new_status)
        current = ClaimStatus(claim.status)
        if target not in TRANSITIONS[current]:
            allowed = ", ".join(s.value for s in TRANSITIONS[current]) or "ninguno (reclamo cerrado)"
            raise InvalidTransitionError(
                f"No se puede pasar de '{current.value}' a '{target.value}'. Estados permitidos: {allowed}"
            )
        note = (note or "").strip() or None
        if target == ClaimStatus.RESUELTO and not note:
            raise InvalidTransitionError(
                "Para marcar un reclamo como resuelto hay que explicar la resolucion en `note`"
            )

        now = datetime.now(timezone.utc)
        claim.status = target.value
        if target == ClaimStatus.RESUELTO:
            claim.resolution = note
            claim.resolved_at = now
        elif target == ClaimStatus.EN_INVESTIGACION:
            # Reabierto: la resolucion anterior ya no aplica.
            claim.resolved_at = None
        elif target == ClaimStatus.CERRADO:
            claim.closed_at = now
        await self.repository.add_event(
            ClaimEvent(
                claim_id=claim.id,
                organization_id=organization_id,
                status=target.value,
                note=note,
                user_id=user_id,
            )
        )
        await self.session.commit()
        return await self._reload(organization_id, claim_number)

    async def _next_number(self, organization_id: uuid.UUID, year: int, offset: int) -> str:
        count = await self.repository.count_for_year(organization_id, year)
        return f"CLM-{year}-{count + 1 + offset:06d}"

    async def _reload(self, organization_id: uuid.UUID, claim_number: str) -> Claim:
        """Vuelve a leer con el historial cargado: la respuesta incluye
        `history`, y en SQLAlchemy async una relacion sin precargar no se
        puede leer fuera de un await."""
        claim = await self.repository.get_by_number(organization_id, claim_number, refresh=True)
        assert claim is not None
        return claim
