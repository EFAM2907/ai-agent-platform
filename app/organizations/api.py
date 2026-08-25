import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_owner
from app.organizations.exceptions import DuplicateTaxIdError
from app.organizations.repository import OrganizationRepository
from app.organizations.schemas import OrganizationBootstrap, OrganizationResponse, OrganizationUpdate
from app.organizations.service import OrganizationService
from app.users.exceptions import UserAlreadyExistsError, UserNotFoundError
from app.users.models import User
from app.users.repository import UserRepository
from app.users.schemas import OwnershipTransfer, UserResponse
from app.users.service import UserService
from app.core.rate_limit import rate_limit

def get_organization_service(session: AsyncSession = Depends(get_db)) -> OrganizationService:
    return OrganizationService(OrganizationRepository(session), session)


router = APIRouter(prefix="/organizations", tags=["organizations"], dependencies=[Depends(rate_limit)])


@router.post("/", response_model=OrganizationResponse, status_code=201)
async def create_organization(data: OrganizationBootstrap, service: OrganizationService = Depends(get_organization_service)):
    try:
        return await service.create_with_owner(data)
    except DuplicateTaxIdError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except UserAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/{organization_id}", response_model=OrganizationResponse)
async def get_organization_by_id(organization_id: uuid.UUID, service: OrganizationService = Depends(get_organization_service), current_user: User = Depends(get_current_user)):
    organization = await service.get_by_id(organization_id)
    if organization is None or organization.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if current_user.organization_id != organization_id and not current_user.is_platform_admin:
        raise HTTPException(status_code=404, detail="Organization not found")
    return organization


@router.patch("/{organization_id}", response_model=OrganizationResponse)
async def update_organization(organization_id: uuid.UUID, data: OrganizationUpdate, service: OrganizationService = Depends(get_organization_service), owner: User = Depends(get_current_user)):
    if owner.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Organization not found")
    require_owner(owner)
    organization = await service.update(organization_id, data)
    if organization is None or organization.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return organization


@router.delete("/{organization_id}", status_code=204)
async def delete_organization(organization_id: uuid.UUID, service: OrganizationService = Depends(get_organization_service), owner: User = Depends(get_current_user)):
    if owner.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Organization not found")
    require_owner(owner)
    organization = await service.delete(organization_id)
    if organization is None or organization.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Organization not found")


@router.get("/", response_model=list[OrganizationResponse])
async def list_organizations(skip: int = 0, limit: int = Query(default=50, le=100), service: OrganizationService = Depends(get_organization_service), current_user: User = Depends(get_current_user)):
    if not current_user.is_platform_admin:
        raise HTTPException(status_code=403, detail="Platform admin privileges required")
    return await service.list(skip, limit)


@router.post("/{organization_id}/transfer-ownership", response_model=UserResponse)
async def transfer_ownership(organization_id: uuid.UUID, data: OwnershipTransfer, session: AsyncSession = Depends(get_db), owner: User = Depends(get_current_user)):
    if owner.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Organization not found")
    require_owner(owner)
    organization = await OrganizationRepository(session).get_by_id(organization_id)
    if organization is None or organization.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Organization not found")
    service = UserService(UserRepository(session), session)
    try:
        return await service.transfer_ownership(owner.id, data.target_user_id)
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail="User not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
