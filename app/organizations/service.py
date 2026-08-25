from sqlalchemy.ext.asyncio import AsyncSession
from app.organizations.schemas import OrganizationBootstrap, OrganizationCreate, OrganizationUpdate
from app.organizations.models import Organization
from app.organizations.repository import OrganizationRepository
from app.organizations.exceptions import DuplicateTaxIdError
from app.core.security import hash_password
from app.users.exceptions import UserAlreadyExistsError
from app.users.models import User, UserRole
from app.users.repository import UserRepository
import uuid
class OrganizationService:
    def __init__(self, repository: OrganizationRepository, session: AsyncSession):
        self.repository = repository
        self.session = session
        
    async def create_with_owner(self, data: OrganizationBootstrap) -> Organization:
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
        return organization
    async def get_by_id(self, organization_id):
        return await self.repository.get_by_id(organization_id)
    

    
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
