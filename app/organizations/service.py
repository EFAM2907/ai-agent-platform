import logging

from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from app.organizations.schemas import OrganizationBootstrap, OrganizationCreate, OrganizationUpdate
from app.organizations.models import Organization
from app.organizations.repository import OrganizationRepository
from app.organizations.exceptions import DuplicateTaxIdError
from app.core.crypto import encrypt_secret
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.llm.errors import TenantVirtualKeyError
from app.users.exceptions import UserAlreadyExistsError
from app.users.models import User, UserRole
from app.users.repository import UserRepository
from app.core.config import settings
from app.llm.litellm_admin import generate_tenant_virtual_key
import uuid

logger = logging.getLogger(__name__)


class OrganizationService:
    def __init__(self, repository: OrganizationRepository, session: AsyncSession):
        self.repository = repository
        self.session = session
        
    async def create_with_owner(
        self, data: OrganizationBootstrap, background_tasks: BackgroundTasks
    ) -> Organization:
        """Create the organization and its initial OWNER in one database transaction."""
        if data.tax_id:
            organization = await self.repository.get_by_tax_id(data.tax_id)
            if organization:
                raise DuplicateTaxIdError(data.tax_id)

        user_repository = UserRepository(self.session)
        existing_user = await user_repository.get_by_email(data.owner_email)
        if existing_user:
            raise UserAlreadyExistsError(data.owner_email)

        organization = await self.repository.create(
            OrganizationCreate(name=data.name, tax_id=data.tax_id)
        )
        owner = User(
            email=data.owner_email,
            hashed_password=hash_password(data.owner_password),
            full_name=data.owner_full_name,
            organization_id=organization.id,
            role=UserRole.OWNER,
        )
        self.session.add(owner)
        await self.session.flush()
        await self.session.commit()

        # Encolada DESPUÉS del commit, nunca antes: el registro no debe
        # depender de que LiteLLM esté disponible (ver
        # provision_llm_key_in_background más abajo para el manejo de
        # errores -- no debe poder afectar esta respuesta HTTP, que ya
        # se está devolviendo al terminar esta función).
        background_tasks.add_task(provision_llm_key_in_background, organization.id)
        return organization
    async def get_by_id(self, organization_id):
        return await self.repository.get_by_id(organization_id)

    async def provision_llm_key(self, organization_id: uuid.UUID) -> Organization | None:
        """Provisiona una virtual key una sola vez, sin exponerla fuera del modelo."""
        organization = await self.repository.get_by_id(organization_id)
        if organization is None or organization.deleted_at is not None:
            return None

        if organization.litellm_virtual_key is not None:
            return organization

        virtual_key = await generate_tenant_virtual_key(
            organization.id,
            settings.default_tenant_monthly_budget,
        )
        # Se cifra antes de persistir -- es un secreto equivalente a una
        # API key real (autentica y gasta contra el budget del tenant).
        organization.litellm_virtual_key = encrypt_secret(virtual_key)
        await self.session.commit()
        return organization
    

    
    async def update(
        self,
        organization_id: uuid.UUID,
        data: OrganizationUpdate
    ) -> Organization | None:

        organization = await self.repository.get_by_id(organization_id)

        if organization is None:
            return None

        changes = data.model_dump(exclude_unset=True)

        updated_organization = await self.repository.update(
            organization,
            changes
        )

        await self.session.commit()

        return updated_organization
        
    async def delete(
        self,
        organization_id: uuid.UUID
    ) -> Organization | None:

        organization = await self.repository.get_by_id(organization_id)

        if organization is None:
            return None

        deleted_organization = await self.repository.delete(organization)

        await self.session.commit()

        return deleted_organization
    
    
    async def list(self, skip: int = 0, limit: int = 50) -> list[Organization]:
        return await self.repository.list(skip, limit)


async def provision_llm_key_in_background(organization_id: uuid.UUID) -> None:
    """Entry point para el BackgroundTask que dispara create_with_owner()
    tras un registro exitoso.

    Abre su propia sesión de DB -- nunca reutiliza la del request, que
    puede cerrarse antes de que este task corra -- y nunca deja
    escapar un fallo de LiteLLM: la respuesta HTTP del registro ya se
    envió al cliente antes de que esto se ejecute, así que un error
    acá solo se loguea con el organization_id, nunca se propaga."""
    try:
        async with SessionLocal() as session:
            service = OrganizationService(OrganizationRepository(session), session)
            await service.provision_llm_key(organization_id)
    except TenantVirtualKeyError as exc:
        logger.error(
            "No se pudo aprovisionar la LLM key en background para la "
            "organizacion %s: %s",
            organization_id,
            exc,
        )
    except Exception:
        logger.exception(
            "Fallo inesperado aprovisionando la LLM key en background "
            "para la organizacion %s",
            organization_id,
        )
