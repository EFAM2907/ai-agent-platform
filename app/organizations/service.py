from sqlalchemy.ext.asyncio import AsyncSession
from app.organizations.schemas import OrganizationCreate, OrganizationUpdate
from app.organizations.models import Organization
from app.organizations.repository import OrganizationRepository
from app.organizations.exceptions import DuplicateTaxIdError
import uuid
class OrganizationService:
    def __init__(self, repository: OrganizationRepository, session: AsyncSession):
        self.repository = repository
        self.session = session
        
    async def create(self, data: OrganizationCreate) -> Organization:
        if data.tax_id:
            org = await self.repository.get_by_tax_id(data.tax_id)
            if org:
                raise DuplicateTaxIdError(data.tax_id)

        organization = await self.repository.create(data)
        await self.session.commit()
        return organization
    async def get_by_id(self, organization_id):
        return await self.repository.get_by_id(organization_id)
    
    async def update(self, organization_id: uuid.UUID, data: OrganizationUpdate) -> Organization | None:
        organization = await self.repository.get_by_id(organization_id)
        if organization is None:
            return None

        changes = data.model_dump(exclude_unset=True)
        return await self.repository.update(organization, changes)
    
    async def delete(self, organization_id : uuid.UUID) -> Organization | None:
        organization = await self.repository.get_by_id(organization_id)
        if organization is None:
             return None
        return await self.repository.delete(organization)
        
    async def list(self, skip: int = 0, limit: int = 50) -> list[Organization]:
         return await self.repository.list(skip, limit)