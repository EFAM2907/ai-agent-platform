from fastapi import APIRouter, Depends,HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.organizations.repository import OrganizationRepository
from app.organizations.service import OrganizationService
from app.organizations.schemas import OrganizationCreate, OrganizationResponse
from app.organizations.exceptions import DuplicateTaxIdError
import uuid


def get_organization_service(session: AsyncSession = Depends(get_db)) -> OrganizationService:
    repository = OrganizationRepository(session)
    return OrganizationService(repository, session)

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.post("/", response_model=OrganizationResponse, status_code=201)
async def create_organization(
    data: OrganizationCreate,
    service: OrganizationService = Depends(get_organization_service)
):
    try:
        organization = await service.create(data)
    except DuplicateTaxIdError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return organization


@router.get("/{organization_id}", response_model= OrganizationResponse)
async def get_organization_by_id(
    organization_id : uuid.UUID,
    service: OrganizationService = Depends(get_organization_service)    
):
    organization = await service.get_by_id(organization_id)
    
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return organization
