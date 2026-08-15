from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.organizations.models import Organization
from app.organizations.schemas import OrganizationCreate


class OrganizationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: OrganizationCreate) -> Organization:
        organization = Organization(
            name=data.name,
            tax_id=data.tax_id,
        )
        self.session.add(organization)
        await self.session.flush()
        return organization

    async def get_by_id(self, organization_id: int) -> Organization | None:
        stmt = select(Organization).where(Organization.id == organization_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
