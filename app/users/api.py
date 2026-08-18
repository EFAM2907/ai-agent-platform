from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.users.repository import UserRepository
from app.users.service import UserService
from app.users.schemas import UserCreate, UserResponse, UserUpdate
from app.users.exceptions import UserAlreadyExistsError, UserNotFoundError
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
    service: UserService = Depends(get_user_service)
):
    user = await service.get_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.get("/", response_model=list[UserResponse])
async def list_users(
    skip: int = 0,
    limit: int = Query(default=50, le=100),
    service: UserService = Depends(get_user_service)
):
    return await service.list(skip, limit)

@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    service: UserService = Depends(get_user_service)
):
    try:
        user = await service.update(user_id, data)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return user

@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: uuid.UUID,
    service: UserService = Depends(get_user_service)
):
    try:
        await service.delete(user_id)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return None
