"""Dependencias de FastAPI que resuelven contexto de organización para
un request autenticado.

get_current_user() (app.core.dependencies) resuelve el User -- que
solo guarda organization_id como FK -- pero no existía hasta ahora un
punto único que cargue la fila completa de Organization (con
litellm_virtual_key incluido) para el usuario autenticado. Se agrega
acá, no en app.core.dependencies, para no acoplar ese módulo (core,
capa de bajo nivel) a repositorios de un dominio concreto: los módulos
de dominio (organizations, users) ya importan de core y de llm, nunca
al revés -- este archivo sigue esa misma dirección.
"""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.llm.client import LLMClient
from app.llm.factory import get_default_llm_client
from app.organizations.models import Organization
from app.organizations.repository import OrganizationRepository
from app.users.models import User

logger = logging.getLogger(__name__)


async def get_current_organization(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Organization:
    repository = OrganizationRepository(session)
    organization = await repository.get_by_id(current_user.organization_id)
    if organization is None or organization.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return organization


def resolve_tenant_virtual_key(organization: Organization) -> str:
    """Con litellm_virtual_key ya aprovisionada, la usa (descifrada)
    para que LiteLLM aplique el budget y el aislamiento de costo del
    tenant. Sin ella (aprovisionamiento en background todavía
    pendiente o fallido), cae a la virtual key de emergencia -- acotada
    a un budget diario bajo y a un solo modelo barato (ver
    scripts/create_emergency_fallback_key.py) -- nunca a la master key,
    que no tiene límite de gasto. Y nunca en silencio: loguea un
    WARNING con el organization_id, porque significa que ese tenant
    está gastando contra un budget compartido, no el suyo propio.

    Extraída de get_tenant_llm_client para que app.rag (embeddings)
    pueda resolver la misma virtual key sin duplicar esta lógica ni
    el WARNING -- chat y embeddings comparten presupuesto por tenant,
    hablan con el mismo gateway."""
    if organization.litellm_virtual_key is not None:
        return decrypt_secret(organization.litellm_virtual_key)

    logger.warning(
        "Organizacion %s sin litellm_virtual_key aprovisionada -- "
        "usando la virtual key de emergencia (budget acotado, "
        "compartido entre tenants) en vez de la propia",
        organization.id,
    )
    return settings.litellm_emergency_fallback_key


async def get_tenant_llm_client(
    organization: Organization = Depends(get_current_organization),
) -> LLMClient:
    """Resuelve el LLMClient a usar para este request -- ver
    resolve_tenant_virtual_key para la lógica de qué virtual key
    usar."""
    return get_default_llm_client(
        tenant_virtual_key=resolve_tenant_virtual_key(organization)
    )
