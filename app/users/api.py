from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.users.repository import UserRepository
from app.users.service import UserService
from app.users.schemas import UserCreate, UserResponse, UserUpdate, UserRoleUpdate
from app.users.exceptions import UserAlreadyExistsError, UserNotFoundError
from app.core.dependencies import get_current_user, require_admin
from app.users.models import User, UserRole
import uuid

def get_user_service(session: AsyncSession = Depends(get_db)) -> UserService:
    repository = UserRepository(session)
    return UserService(repository, session)

router = APIRouter(prefix="/users", tags=["users"])

@router.post("/", response_model=UserResponse, status_code=201)
async def create_user(
    data: UserCreate,
    service: UserService = Depends(get_user_service)
):
    try:
        user = await service.create(data)
    except UserAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return user

@router.get("/{user_id}", response_model=UserResponse)
async def get_user_by_id(
    user_id: uuid.UUID,
    service: UserService = Depends(get_user_service),
    current_user: User = Depends(get_current_user)
):
    # Un usuario común solo puede consultarse a sí mismo, a menos que sea ADMIN
    if current_user.id != user_id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Not authorized to view this user")

    user = await service.get_by_id(user_id)
    if user is None or user.deleted_at is not None:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.get("/", response_model=list[UserResponse])
async def list_users(
    skip: int = 0,
    limit: int = Query(default=50, le=100),
    service: UserService = Depends(get_user_service),
    admin: User = Depends(require_admin)
):

    return await service.list(admin.organization_id, skip, limit)

@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    service: UserService = Depends(get_user_service),
    current_user: User = Depends(get_current_user)
):
    # Un usuario solo puede modificarse a sí mismo, a menos que sea ADMIN
    if current_user.id != user_id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Not authorized to update this user")

    try:
        user = await service.update(user_id, data)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return user

@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: uuid.UUID,
    service: UserService = Depends(get_user_service),
    current_user: User = Depends(get_current_user)
):
    # Un usuario solo puede eliminarse a sí mismo, a menos que sea ADMIN
    if current_user.id != user_id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Not authorized to delete this user")

    try:
        await service.delete(user_id)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return None


@router.patch("/{user_id}/role", response_model=UserResponse)
async def change_role(
    user_id: uuid.UUID,
    role_update: UserRoleUpdate,
    service: UserService = Depends(get_user_service),
    admin: User = Depends(require_admin)
):
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="You cannot change your own role")

    try:
        return await service.update_role(user_id, role_update.role)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
