"""Pipeline de atencion por correo, una organizacion a la vez:

  1. Lee el correo (Gmail API)
  2. Extrae el numero de guia (candidatos por regex, validados en la BD)
  3. Consulta el envio en la base de datos de la organizacion
  4. Obtiene estado y ruta ("En transito - Medellin -> Bogota")
  5. Redacta la respuesta (LLM sobre hechos ya verificados)
  6. Responde por Gmail AL MISMO REMITENTE, en el mismo hilo

Criterio de a quien se responde (el buzon conectado puede ser uno real,
con correo personal o de terceros, no solo de clientes):
  - Con el estado del envio: solo si el correo trae una guia que EXISTE
    en la organizacion Y quien escribe es el cliente de ese envio (su
    correo coincide con Customer.email y Gmail aprobo SPF/DKIM/DMARC).
  - "No encontramos esa guia": si trae una guia explicita ("guia
    123456") que no existe o que no es de quien escribe -- el mismo
    texto para ambos casos, para no confirmar que una guia ajena existe.
  - "Necesitamos tu numero de guia": si no trae ninguna guia pero
    claramente pregunta por el estado de un envio (ver
    tracking.looks_like_status_inquiry), con tope de 1 por dia.
  - Cualquier otro correo se ignora.
  - Nunca a remitentes automaticos (noreply, listas, autorespuestas,
    mailer-daemon) ni al propio buzon, y con tope diario por remitente.
  - Un mismo correo jamas se responde dos veces (ver
    GmailRepository.claim_message).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from app.core.config import settings
from app.gmail.client import GmailClient
from app.gmail.errors import GmailAccessRevokedError
from app.gmail.models import (
    OUTCOME_FAILED,
    OUTCOME_REPLIED,
    OUTCOME_SKIPPED,
    GmailConnection,
)
from app.gmail.parser import ParsedEmail, parse_message
from app.gmail.reply import (
    ReplyComposer,
    ShipmentFacts,
    build_reply_raw,
    render_not_found_body,
    render_tracking_request_body,
)
from app.gmail.repository import GmailRepository
from app.gmail.tracking import extract_tracking_candidates, looks_like_status_inquiry
from app.shipments.models import Shipment
from app.shipments.service import ShipmentService

logger = logging.getLogger(__name__)

# Guias distintas que se contestan en un mismo correo.
MAX_SHIPMENTS_PER_REPLY = 3

# Tipos de respuesta; se guardan en GmailProcessedMessage.detail y sirven
# para topar cada tipo por separado.
KIND_STATUS = "status"
KIND_NOT_FOUND = "not_found"
KIND_TRACKING_REQUEST = "tracking_request"


@dataclass(frozen=True)
class ReplyPlan:
    kind: str
    body: str
    tracking_numbers: list[str]


class MailGateway(Protocol):
    """Lo que el procesador necesita de Gmail (GmailClient lo cumple;
    los tests inyectan un doble)."""

    async def list_message_ids(self, query: str, max_results: int) -> list[str]: ...
    async def get_message(self, message_id: str) -> dict: ...
    async def send_message(self, raw_base64url: str, thread_id: str | None) -> str: ...


@dataclass
class PollSummary:
    examined: int = 0
    replied: int = 0
    skipped: int = 0
    failed: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)

    def count_skip(self, reason: str) -> None:
        self.skipped += 1
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1


class InboxProcessor:
    def __init__(
        self,
        *,
        repository: GmailRepository,
        shipment_service: ShipmentService,
        composer: ReplyComposer,
    ) -> None:
        self._repo = repository
        self._shipments = shipment_service
        self._composer = composer

    async def process(self, connection: GmailConnection, gmail: MailGateway) -> PollSummary:
        summary = PollSummary()
        query = f"in:inbox newer_than:{settings.gmail_poll_lookback}"
        message_ids = await gmail.list_message_ids(query, settings.gmail_max_messages_per_poll)

        for message_id in message_ids:
            if await self._repo.is_finished(connection.id, message_id):
                continue
            summary.examined += 1
            try:
                await self._handle_message(connection, gmail, message_id, summary)
            except GmailAccessRevokedError:
                raise  # el token murio: seguir con mas correos no tiene sentido
            except Exception:
                # Un correo defectuoso nunca debe frenar a los demas.
                logger.exception(
                    "Error inesperado procesando el correo %s de la organizacion %s",
                    message_id,
                    connection.organization_id,
                )
                summary.failed += 1
        return summary

    async def _handle_message(
        self,
        connection: GmailConnection,
        gmail: MailGateway,
        message_id: str,
        summary: PollSummary,
    ) -> None:
        raw = await gmail.get_message(message_id)
        email = parse_message(raw)

        row = await self._repo.claim_message(
            connection,
            message_id=message_id,
            thread_id=email.thread_id,
            sender=email.from_address or None,
        )
        if row is None:
            return  # otro proceso lo esta atendiendo o ya lo termino

        try:
            skip_reason = await self._skip_reason(connection, email)
            if skip_reason is not None:
                await self._repo.finish_message(row, OUTCOME_SKIPPED, detail=skip_reason)
                summary.count_skip(skip_reason)
                return

            plan = await self._build_reply_plan(connection, email)
            if plan is None:
                await self._repo.finish_message(row, OUTCOME_SKIPPED, detail="no_tracking_number")
                summary.count_skip("no_tracking_number")
                return

            if plan.kind == KIND_TRACKING_REQUEST:
                requests = await self._repo.count_recent_replies_to(
                    connection.id, email.from_address, detail=KIND_TRACKING_REQUEST
                )
                if requests >= settings.gmail_max_guide_requests_per_sender_per_day:
                    await self._repo.finish_message(
                        row, OUTCOME_SKIPPED, detail="tracking_request_rate_limited"
                    )
                    summary.count_skip("tracking_request_rate_limited")
                    return

            raw_reply = build_reply_raw(
                original=email, from_email=connection.google_email, body=plan.body
            )
            await gmail.send_message(raw_reply, email.thread_id)
            await self._repo.finish_message(
                row,
                OUTCOME_REPLIED,
                detail=plan.kind,
                tracking_number=",".join(plan.tracking_numbers) or None,
            )
            summary.replied += 1
        except GmailAccessRevokedError:
            await self._repo.finish_message(row, OUTCOME_FAILED, detail="access_revoked")
            raise
        except Exception as exc:
            # Solo el tipo de la excepcion: el mensaje puede traer
            # fragmentos del correo o de la respuesta.
            await self._repo.finish_message(row, OUTCOME_FAILED, detail=type(exc).__name__)
            summary.failed += 1
            logger.exception(
                "Fallo respondiendo el correo %s de la organizacion %s",
                message_id,
                connection.organization_id,
            )

    async def _skip_reason(self, connection: GmailConnection, email: ParsedEmail) -> str | None:
        if not email.from_address:
            return "invalid_sender"
        if email.from_address == connection.google_email.lower():
            return "own_message"
        if email.is_automated:
            return "automated_sender"
        recent = await self._repo.count_recent_replies_to(connection.id, email.from_address)
        if recent >= settings.gmail_max_replies_per_sender_per_day:
            return "sender_rate_limited"
        return None

    async def _build_reply_plan(
        self, connection: GmailConnection, email: ParsedEmail
    ) -> ReplyPlan | None:
        """Que contestar, o None si el correo no trae nada que contestar."""
        # El asunto tambien cuenta: muchos clientes escriben "Guia 123456"
        # solo ahi.
        text = f"{email.subject}\n{email.body}"
        candidates = extract_tracking_candidates(text)

        facts: list[ShipmentFacts] = []
        for candidate in candidates:
            shipment = await self._find_shipment(connection.organization_id, candidate.number)
            if shipment is not None and self._sender_owns(email, shipment):
                facts.append(ShipmentFacts.from_shipment(shipment))
            if len(facts) == MAX_SHIPMENTS_PER_REPLY:
                break

        if facts:
            body = await self._composer.compose_tracking_reply(facts, email.from_name)
            return ReplyPlan(KIND_STATUS, body, [f.tracking_number for f in facts])

        explicit = [c.number for c in candidates if c.explicit]
        if explicit:
            return ReplyPlan(
                KIND_NOT_FOUND, render_not_found_body(explicit, email.from_name), explicit
            )
        if looks_like_status_inquiry(text):
            return ReplyPlan(
                KIND_TRACKING_REQUEST, render_tracking_request_body(email.from_name), []
            )
        return None

    async def _find_shipment(self, organization_id: uuid.UUID, number: str) -> Shipment | None:
        """Busca la guia tal como llego y, si trae letras, en mayuscula y
        minuscula: los clientes no respetan el formato de la guia."""
        for variant in dict.fromkeys((number, number.upper(), number.lower())):
            shipment = await self._shipments.get_by_tracking_number(organization_id, variant)
            if shipment is not None:
                return shipment
        return None

    @staticmethod
    def _sender_owns(email: ParsedEmail, shipment: Shipment) -> bool:
        """Solo el cliente del envio recibe su estado. Sin correo del
        cliente registrado no hay contra que verificar, y sin
        autenticacion el From se falsifica con una linea: en ambos casos
        se niega (fail-closed)."""
        if not settings.gmail_require_sender_match:
            return True
        customer_email = (shipment.customer.email or "").strip().lower()
        return (
            bool(customer_email)
            and customer_email == email.from_address
            and email.sender_authenticated
        )
