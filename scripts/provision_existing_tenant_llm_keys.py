"""Provisiona virtual keys de LiteLLM para organizaciones activas existentes.

Dos casos de uso, no solo uno:
1. Backfill legacy: organizaciones creadas antes de que existiera el
   aprovisionamiento automático (ver provision_llm_key_in_background en
   app.organizations.service), que nunca tuvieron oportunidad de
   generar su key.
2. Red de seguridad para el aprovisionamiento automático: create_with_owner()
   y AuthService.register() encolan un BackgroundTask que llama a
   provision_llm_key() justo después del registro, pero si LiteLLM está
   caído en ese momento, ese intento falla y solo queda logueado (nunca
   bloquea ni reintenta la respuesta HTTP ya enviada). Este script,
   corrido periódicamente (cron), es lo que efectivamente reintenta esos
   casos -- es idempotente (provision_llm_key() no regenera si ya existe
   una key), así que correrlo de más nunca es un problema.
"""

from __future__ import annotations

import asyncio

from app.core.database import SessionLocal
from app.organizations.repository import OrganizationRepository
from app.organizations.service import OrganizationService


async def provision_existing_tenant_llm_keys() -> tuple[int, int]:
    generated = 0
    already_present = 0

    async with SessionLocal() as session:
        repository = OrganizationRepository(session)
        service = OrganizationService(repository, session)
        organizations = await repository.list_all_active()

        for organization in organizations:
            if organization.litellm_virtual_key is not None:
                already_present += 1
                continue
            await service.provision_llm_key(organization.id)
            generated += 1

    return generated, already_present


async def main() -> None:
    generated, already_present = await provision_existing_tenant_llm_keys()
    print(
        "Aprovisionamiento terminado: "
        f"{generated} keys generadas; {already_present} organizaciones ya tenían key."
    )


if __name__ == "__main__":
    asyncio.run(main())
