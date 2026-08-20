from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.users.repository import UserRepository
from app.auth.service import AuthService
from app.auth.schemas import LoginRequest, TokenResponse
from app.core.exceptions import InvalidCredentialsError
import uuid


def get_auth_service(session: AsyncSession = Depends(get_db)) -> AuthService:
    repository = UserRepository(session)
    return AuthService(repository)

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/login", response_model= TokenResponse)
async def get_token_response(
    data: LoginRequest,
    service: AuthService = Depends(get_auth_service)
):
    try:
        token = await service.login(data)
    except InvalidCredentialsError as e:
        raise HTTPException(status_code=401, detail=str(e))
    return TokenResponse(access_token = token)