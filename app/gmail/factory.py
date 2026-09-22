"""Ensamblado del InboxProcessor de una organizacion, compartido por el
endpoint POST /gmail/poll y por scripts/run_gmail_poller.py."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.service import CHEAP_MODEL
from app.gmail.processor import InboxProcessor
from app.gmail.reply import ReplyComposer
from app.gmail.repository import GmailRepository
from app.llm.client import LLMClient
from app.shipments.repository import ShipmentRepository
from app.shipments.service import ShipmentService


def build_inbox_processor(
    session: AsyncSession, llm_client: LLMClient, organization_id: uuid.UUID
) -> InboxProcessor:
    # Redactar una respuesta corta sobre hechos ya resueltos no necesita
    # el modelo caro: siempre el tier barato, sin pasar por ModelRouter.
    return InboxProcessor(
        repository=GmailRepository(session),
        shipment_service=ShipmentService(ShipmentRepository(session), session),
        composer=ReplyComposer(llm_client, CHEAP_MODEL, tenant_id=str(organization_id)),
    )
