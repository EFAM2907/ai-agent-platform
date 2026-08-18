from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.organizations.models import Organization
from app.organizations.schemas import OrganizationCreate
from datetime import datetime, timezone
import uuid

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

    async def get_by_id(self, organization_id: uuid.UUID) -> Organization | None:
        stmt = select(Organization).where(Organization.id == organization_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


    async def get_by_tax_id(self, tax_id: str) -> Organization | None:
        stmt = select(Organization).where(Organization.tax_id == tax_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def update(self, organization: Organization, data: dict) -> Organization:
        for key, value in data.items():
            setattr(organization, key, value)
        await self.session.flush()
        return organization
    
    async def delete(self, organization: Organization) -> Organization:
        organization.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        return organization
    
    async def list(self, skip: int = 0, limit: int = 50) -> list[Organization]:
        stmt = (
            select(Organization)
            .where(Organization.deleted_at.is_(None))
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())