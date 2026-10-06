"""Atiende periodicamente los buzones de Gmail conectados.

Recorre las conexiones activas de todas las organizaciones y, para cada
una, corre el pipeline de app.gmail.processor (leer -> extraer guia ->
consultar envio -> redactar -> responder al mismo remitente).

  python -m scripts.run_gmail_poller          # bucle continuo
  python -m scripts.run_gmail_poller --once   # una sola pasada (cron)

Es seguro correrlo junto a POST /gmail/poll o en varias instancias: cada
correo se reclama atomicamente antes de responderse, nunca se contesta
dos veces. Un fallo en una organizacion no frena a las demas.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from app.core.config import settings
from app.core.database import SessionLocal
from app.gmail.errors import GmailError, GmailNotConnectedError
from app.gmail.factory import build_inbox_processor
from app.gmail.repository import GmailRepository
from app.gmail.service import GmailPollService
from app.llm.factory import get_default_llm_client
from app.organizations.dependencies import resolve_tenant_virtual_key
from app.organizations.repository import OrganizationRepository

logger = logging.getLogger("gmail_poller")


async def poll_all_connections() -> None:
    async with SessionLocal() as session:
        connections = await GmailRepository(session).list_active_connections()
        organization_ids = [c.organization_id for c in connections]

    for organization_id in organization_ids:
        # Una sesion por organizacion: un rollback o error en una no
        # contamina a las siguientes.
        async with SessionLocal() as session:
            repo = GmailRepository(session)
            connection = await repo.get_connection(organization_id)
            if connection is None or connection.revoked_at is not None:
                continue
            try:
                organization = await OrganizationRepository(session).get_by_id(
                    connection.organization_id
                )
                if organization is None or organization.deleted_at is not None:
                    continue
                llm_client = get_default_llm_client(
                    tenant_virtual_key=resolve_tenant_virtual_key(organization)
                )
                processor = build_inbox_processor(session, llm_client, organization.id)
                summary = await GmailPollService(repo, session).poll(connection, processor)
                if summary.examined:
                    logger.info(
                        "org=%s examinados=%d respondidos=%d omitidos=%d fallidos=%d",
                        organization.id,
                        summary.examined,
                        summary.replied,
                        summary.skipped,
                        summary.failed,
                    )
            except GmailNotConnectedError as exc:
                logger.warning("org=%s: %s", connection.organization_id, exc)
            except GmailError:
                logger.exception("org=%s: fallo de Gmail", connection.organization_id)
            except Exception:
                logger.exception("org=%s: fallo inesperado", connection.organization_id)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if "--once" in sys.argv:
        await poll_all_connections()
        return
    logger.info("Poller de Gmail iniciado (cada %ss)", settings.gmail_poll_interval_seconds)
    while True:
        await poll_all_connections()
        await asyncio.sleep(settings.gmail_poll_interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
