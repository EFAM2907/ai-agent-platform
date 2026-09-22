from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class GmailAuthorizationOut(BaseModel):
    authorization_url: str


class GmailStatusOut(BaseModel):
    connected: bool
    google_email: str | None = None
    connected_at: datetime | None = None
    last_polled_at: datetime | None = None


class GmailPollOut(BaseModel):
    examined: int
    replied: int
    skipped: int
    failed: int
    skip_reasons: dict[str, int]


class GmailUnresolvedMessageOut(BaseModel):
    """Un correo que llego y quedo SIN respuesta util para quien lo
    escribio (SKIPPED) o que fallo al enviarse (FAILED) -- ver
    GmailRepository.list_unresolved. No incluye asunto ni cuerpo: eso
    nunca se persiste (ver GmailProcessedMessage); `gmail_link` es la
    unica forma de leerlo, abriendo el correo real en el buzon
    conectado."""

    id: uuid.UUID
    sender: str | None
    tracking_number: str | None
    outcome: str
    detail: str | None
    attempts: int
    processed_at: datetime
    gmail_link: str | None


class GmailUnresolvedListOut(BaseModel):
    total: int
    returned: int
    messages: list[GmailUnresolvedMessageOut]
