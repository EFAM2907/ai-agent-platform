from fastapi import APIRouter, Depends,HTTPException,Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.organizations.repository import OrganizationRepository
from app.organizations.service import OrganizationService
from app.organizations.schemas import OrganizationCreate, OrganizationResponse, OrganizationUpdate
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

@router.patch("/{organization_id}", response_model=OrganizationResponse)
async def update_organization(
    organization_id: uuid.UUID,
    data: OrganizationUpdate,
    service: OrganizationService = Depends(get_organization_service)
):
    organization = await service.update(organization_id, data)
    
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    
    return organization


@router.delete("/{organization_id}", status_code=204)
async def delete_organization(
    organization_id: uuid.UUID,
    service: OrganizationService = Depends(get_organization_service)
):
    organization = await service.delete(organization_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    
    return None

@router.get("/", response_model=list[OrganizationResponse])
async def list_organizations(
    skip: int = 0,
    limit: int = Query(default=50, le=100),
    service: OrganizationService = Depends(get_organization_service)
):
    return await service.list(skip, limit)