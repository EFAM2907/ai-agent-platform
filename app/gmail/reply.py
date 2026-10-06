"""Redaccion y armado de la respuesta por correo.

Los HECHOS (estado, ruta, ultimo movimiento) salen de la base de datos y
se formatean de forma determinista (ShipmentFacts); el LLM solo los
redacta con buen tono. Si el LLM falla, o devuelve un texto que omite
la linea de estado o la guia (alucinacion / reformulacion indebida), se
usa una plantilla fija: el cliente siempre recibe informacion correcta,
nunca la de un modelo que "improviso".

Lo que se incluye del envio es deliberadamente minimo: estado, ruta y
ultimo movimiento. NUNCA datos del cliente (cedula, telefono, correo).
Ademas, el procesador solo llega hasta aca si quien escribe es el
cliente del envio (ver InboxProcessor._sender_owns).
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage

from app.gmail.parser import ParsedEmail
from app.llm.client import LLMClient
from app.llm.errors import LLMError
from app.llm.prompts.loader import PromptLoader
from app.llm.schemas import LLMRequest, Message, Role
from app.shipments.models import Shipment, ShipmentStatus

logger = logging.getLogger(__name__)

STATUS_LABELS = {
    ShipmentStatus.ADMITIDO: "Admitido",
    ShipmentStatus.EN_BODEGA_ORIGEN: "En bodega de origen",
    ShipmentStatus.EN_TRANSITO: "En tránsito",
    ShipmentStatus.EN_BODEGA_DESTINO: "En bodega de destino",
    ShipmentStatus.EN_REPARTO: "En reparto",
    ShipmentStatus.ENTREGADO: "Entregado",
    ShipmentStatus.NOVEDAD: "Novedad",
}

_PROMPT_NAME = "gmail_tracking_reply"


@dataclass(frozen=True)
class ShipmentFacts:
    tracking_number: str
    # "En tránsito - Medellín → Bogotá": es la linea que el LLM debe
    # reproducir textualmente y que se verifica en su salida.
    status_line: str
    last_event_city: str | None
    last_event_at: datetime | None
    last_event_notes: str | None

    @classmethod
    def from_shipment(cls, shipment: Shipment) -> "ShipmentFacts":
        label = STATUS_LABELS[shipment.status]
        last = shipment.events[-1] if shipment.events else None
        return cls(
            tracking_number=shipment.tracking_number,
            status_line=f"{label} - {shipment.origin_city} → {shipment.destination_city}",
            last_event_city=last.city if last else None,
            last_event_at=last.occurred_at if last else None,
            last_event_notes=last.notes if last else None,
        )

    def as_text(self) -> str:
        lines = [f"Guía: {self.tracking_number}", f"Estado: {self.status_line}"]
        if self.last_event_at is not None:
            where = f" en {self.last_event_city}" if self.last_event_city else ""
            lines.append(f"Último movimiento: {self.last_event_at:%d/%m/%Y %H:%M}{where}")
        if self.last_event_notes:
            lines.append(f"Notas: {self.last_event_notes}")
        return "\n".join(lines)


def render_fallback_body(facts: list[ShipmentFacts], sender_name: str) -> str:
    greeting = f"Hola {sender_name}," if sender_name else "Hola,"
    blocks = "\n\n".join(f.as_text() for f in facts)
    return (
        f"{greeting}\n\nEste es el estado actual de tu envío:\n\n{blocks}\n\n"
        "Si necesitas más ayuda, responde a este correo.\n\nSaludos cordiales."
    )


def render_not_found_body(tracking_numbers: list[str], sender_name: str) -> str:
    """Mismo texto para "la guia no existe" y "existe pero no es de quien
    escribe": distinguirlos le confirmaria a un tercero que la guia es
    real."""
    sender_name = _safe_name(sender_name)
    greeting = f"Hola {sender_name}," if sender_name else "Hola,"
    numbers = ", ".join(tracking_numbers)
    return (
        f"{greeting}\n\nNo encontramos ningún envío con la guía {numbers} asociado a este "
        "correo. Por favor verifica el número y escríbenos desde el correo electrónico "
        "registrado en el envío.\n\nSaludos cordiales."
    )


def render_tracking_request_body(sender_name: str) -> str:
    """Respuesta a quien pregunta por su envio pero no dio ninguna guia."""
    sender_name = _safe_name(sender_name)
    greeting = f"Hola {sender_name}," if sender_name else "Hola,"
    return (
        f"{greeting}\n\nGracias por escribirnos. Para consultar el estado de tu envío "
        "necesitamos el número de guía. Respóndenos a este correo indicándolo "
        "(por ejemplo: \"guía 123456\") y te compartiremos el estado.\n\n"
        "Por seguridad, solo damos información del envío al correo electrónico "
        "registrado en él.\n\nSaludos cordiales."
    )


def _safe_name(name: str) -> str:
    """El nombre del remitente es texto de un tercero que llega al
    prompt: una linea corta, sin saltos ni llaves."""
    return re.sub(r"[\r\n{}]+", " ", name).strip()[:60]


class ReplyComposer:
    def __init__(
        self,
        llm_client: LLMClient,
        model: str,
        *,
        tenant_id: str | None = None,
        prompt_loader: PromptLoader | None = None,
        reasoning_effort: str | None = "none",
    ) -> None:
        self._llm = llm_client
        self._model = model
        self._tenant_id = tenant_id
        self._prompts = prompt_loader or PromptLoader()
        self._reasoning_effort = reasoning_effort

    async def compose_tracking_reply(
        self, facts: list[ShipmentFacts], sender_name: str
    ) -> str:
        name = _safe_name(sender_name)
        try:
            prompt = self._prompts.load_latest(_PROMPT_NAME).render(
                sender_name=name,
                shipments_info="\n\n".join(f.as_text() for f in facts),
            )
            response = await self._llm.generate(
                LLMRequest(
                    messages=[
                        Message(role=Role.SYSTEM, content=prompt),
                        Message(role=Role.USER, content="Redacta la respuesta al cliente."),
                    ],
                    model=self._model,
                    temperature=0.2,
                    reasoning_effort=self._reasoning_effort,  # type: ignore[arg-type]
                    tenant_id=self._tenant_id,
                    request_tag="gmail_tracking_reply",
                )
            )
            body = response.content.strip()
        except (LLMError, FileNotFoundError, ValueError):
            logger.warning(
                "El LLM no pudo redactar la respuesta de tracking; usando la plantilla fija",
                exc_info=True,
            )
            return render_fallback_body(facts, name)

        if not _preserves_facts(body, facts):
            logger.warning(
                "La respuesta del LLM omitio la guia o la linea de estado; usando la plantilla fija"
            )
            return render_fallback_body(facts, name)
        return body


def _preserves_facts(body: str, facts: list[ShipmentFacts]) -> bool:
    return bool(body) and all(
        f.tracking_number in body and f.status_line in body for f in facts
    )


def _reply_subject(subject: str) -> str:
    subject = subject or "Estado de tu envío"
    return subject if re.match(r"(?i)^re:", subject) else f"Re: {subject}"


def build_reply_raw(*, original: ParsedEmail, from_email: str, body: str) -> str:
    """Arma el correo de respuesta, listo para la Gmail API (base64url).

    Va SOLO a `original.from_address` -- el remitente del correo, no
    Reply-To ni CC: una cabecera Reply-To falsificada podria redirigir
    informacion de envios a un tercero. In-Reply-To/References mantienen
    la conversacion en el mismo hilo, y Auto-Submitted (RFC 3834) hace
    que autorespondedores y otros bots como este NO nos contesten de
    vuelta."""
    message = EmailMessage()
    message["From"] = from_email
    message["To"] = original.from_address
    message["Subject"] = _reply_subject(original.subject)
    if original.rfc_message_id:
        message["In-Reply-To"] = original.rfc_message_id
        refs = f"{original.references} {original.rfc_message_id}" if original.references else original.rfc_message_id
        message["References"] = refs
    message["Auto-Submitted"] = "auto-replied"
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode()
