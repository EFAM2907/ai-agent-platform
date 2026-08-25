import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import can_manage_other_user, get_current_user, require_admin, verify_same_organization
from app.users.exceptions import UserAlreadyExistsError, UserNotFoundError
from app.users.models import User, UserRole
from app.users.repository import UserRepository
from app.users.schemas import UserCreate, UserResponse, UserRoleUpdate, UserUpdate, OwnershipTransfer
from app.users.service import UserService
from app.core.rate_limit import rate_limit


def get_user_service(session: AsyncSession = Depends(get_db)) -> UserService:
    return UserService(UserRepository(session), session)


router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(rate_limit)])


@router.post("/", response_model=UserResponse, status_code=201)
async def create_user(data: UserCreate, service: UserService = Depends(get_user_service), admin: User = Depends(require_admin)):
    try:
        return await service.create(admin, data)
    except UserAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/{user_id}", response_model=UserResponse)
async def get_user_by_id(user_id: uuid.UUID, service: UserService = Depends(get_user_service), current_user: User = Depends(get_current_user)):
    user = await service.get_by_id(user_id)
    if user is None or user.deleted_at is not None:
        raise HTTPException(status_code=404, detail="User not found")
    verify_same_organization(current_user, user.organization_id)
    if current_user.id != user_id and current_user.role not in (UserRole.ADMIN, UserRole.OWNER) and not current_user.is_platform_admin:
        raise HTTPException(status_code=403, detail="Not authorized to view this user")
    return user


@router.get("/", response_model=list[UserResponse])
async def list_users(skip: int = 0, limit: int = Query(default=50, le=100), service: UserService = Depends(get_user_service), admin: User = Depends(require_admin)):
    return await service.list(admin.organization_id, skip, limit)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(user_id: uuid.UUID, data: UserUpdate, service: UserService = Depends(get_user_service), current_user: User = Depends(get_current_user)):
    target = await service.get_by_id(user_id)
    if target is None or target.deleted_at is not None:
        raise HTTPException(status_code=404, detail="User not found")
    verify_same_organization(current_user, target.organization_id)
    if current_user.id != user_id and not can_manage_other_user(current_user, target):
        raise HTTPException(status_code=403, detail="Not authorized to update this user")
    return await service.update(user_id, data)


@router.delete("/{user_id}", status_code=204)
async def delete_user(user_id: uuid.UUID, service: UserService = Depends(get_user_service), current_user: User = Depends(get_current_user)):
    target = await service.get_by_id(user_id)
    if target is None or target.deleted_at is not None:
        raise HTTPException(status_code=404, detail="User not found")
    verify_same_organization(current_user, target.organization_id)
    if current_user.id == user_id and current_user.role == UserRole.OWNER:
        raise HTTPException(status_code=400, detail="Transfer ownership before deleting the owner account")
    if current_user.id != user_id and not can_manage_other_user(current_user, target):
        raise HTTPException(status_code=403, detail="Not authorized to delete this user")
    await service.delete(user_id)


@router.patch("/{user_id}/role", response_model=UserResponse)
async def change_role(user_id: uuid.UUID, role_update: UserRoleUpdate, service: UserService = Depends(get_user_service), admin: User = Depends(require_admin)):
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="You cannot change your own role")
    target = await service.get_by_id(user_id)
    if target is None or target.deleted_at is not None:
        raise HTTPException(status_code=404, detail="User not found")
    verify_same_organization(admin, target.organization_id)
    if target.role == UserRole.OWNER or role_update.role == UserRole.OWNER:
        raise HTTPException(status_code=400, detail="Use the ownership transfer endpoint for OWNER")
    if not can_manage_other_user(admin, target):
        raise HTTPException(status_code=403, detail="Not authorized to change this user's role")
    if admin.role == UserRole.ADMIN and role_update.role == UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admins can only assign roles below ADMIN")
    return await service.update_role(user_id, role_update.role)


