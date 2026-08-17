from sqlalchemy.ext.asyncio import AsyncSession
from app.organizations.schemas import OrganizationCreate
from app.organizations.models import Organization
from app.organizations.repository import OrganizationRepository
from app.organizations.exceptions import DuplicateTaxIdError


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