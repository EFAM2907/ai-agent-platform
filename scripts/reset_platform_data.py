"""Reset completo de datos multi-tenant: elimina TODAS las organizaciones,
usuarios y todo lo que depende de ellas (tokens, sesiones/mensajes de
chat, articulos/chunks de KB), y deja la plataforma con un solo tenant
real: Inter Rapidisimo, con Edwin Arias como su primer OWNER.

Uso:
    python -m scripts.reset_platform_data

Es destructivo e irreversible -- no pide confirmacion a proposito, porque
esta pensado para correrse una sola vez en el pivote de "empresas de
prueba genericas" a "tenants de negocio reales". No correrlo en un
ambiente que tenga datos que importe conservar.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.llm.errors import TenantVirtualKeyError
from app.organizations.models import Organization
from app.organizations.repository import OrganizationRepository
from app.organizations.service import OrganizationService
from app.users.models import User, UserRole

OWNER_EMAIL = "efam2907@gmail.com"
OWNER_PASSWORD = "Efam12345"
OWNER_FULL_NAME = "Edwin Arias"
ORG_NAME = "Inter Rapidisimo"
# NIT ficticio -- este proyecto usa datos ilustrativos inspirados en el
# sector logistico, no datos reales de la empresa. Cambialo si prefieres
# otro valor.
ORG_TAX_ID = "901234567-8"


async def reset_platform_data() -> Organization:
    async with SessionLocal() as session:
        # CASCADE se encarga de kb_chunks, chat_messages, chat_sessions y
        # refresh_tokens aunque no los nombremos -- todos tienen una FK
        # hacia organizations o users. TRUNCATE (no DELETE) porque no
        # importa preservar ningun dato, solo dejar las tablas vacias.
        await session.execute(
            text("TRUNCATE TABLE organizations, users, kb_articles CASCADE")
        )
        await session.commit()

        organization = Organization(name=ORG_NAME, tax_id=ORG_TAX_ID)
        session.add(organization)
        await session.flush()

        owner = User(
            email=OWNER_EMAIL,
            hashed_password=hash_password(OWNER_PASSWORD),
            full_name=OWNER_FULL_NAME,
            organization_id=organization.id,
            role=UserRole.OWNER,
        )
        session.add(owner)
        await session.commit()

        # Aprovisiona la virtual key de LiteLLM ya mismo (no en background
        # como en el flujo normal de /auth/register) -- este script corre
        # una sola vez y la necesitamos lista antes de poder ingerir la KB
        # nueva (embeddings) o hablar con el LLM desde el frontend.
        org_repository = OrganizationRepository(session)
        service = OrganizationService(org_repository, session)
        try:
            await service.provision_llm_key(organization.id)
        except TenantVirtualKeyError as exc:
            print(f"Aviso: no se pudo aprovisionar la virtual key de LiteLLM todavia ({exc}).")
            print("Corre despues (con Docker/LiteLLM arriba):")
            print("  python -m scripts.provision_existing_tenant_llm_keys")

        return organization


async def main() -> None:
    organization = await reset_platform_data()
    print("Reset completo.")
    print(f"Organizacion creada: {organization.name} ({organization.id})")
    print(f"Owner: {OWNER_EMAIL} / {OWNER_PASSWORD}")
    print()
    print("Siguiente paso -- ingerir la KB de Inter Rapidisimo:")
    print(
        f"  python -m scripts.ingest_kb_articles {organization.id} --dir inter_rapidisimo"
    )


if __name__ == "__main__":
    asyncio.run(main())
