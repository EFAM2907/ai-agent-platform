from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.users.repository import UserRepository
from app.auth.repository import RefreshTokenRepository
from app.auth.service import AuthService
from app.auth.schemas import LoginRequest, TokenPair, RefreshRequest
from app.core.exceptions import InvalidCredentialsError, InvalidTokenError
from app.organizations.repository import OrganizationRepository
from app.organizations.schemas import OrganizationBootstrap
from app.organizations.exceptions import DuplicateTaxIdError
from app.users.exceptions import UserAlreadyExistsError
from app.core.rate_limit import login_rate_limit, refresh_rate_limit
import uuid



def get_auth_service(session: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(
        UserRepository(session),
        RefreshTokenRepository(session),
        OrganizationRepository(session),
        session
    )

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenPair, dependencies=[Depends(login_rate_limit)])
async def login(credentials: LoginRequest, service: AuthService = Depends(get_auth_service)):
    try:
        return await service.login(credentials)
    except InvalidCredentialsError as e:
        raise HTTPException(status_code=401, detail=str(e))
    
@router.post("/refresh", response_model=TokenPair, dependencies=[Depends(refresh_rate_limit)])
async def refresh_token(data: RefreshRequest, service: AuthService = Depends(get_auth_service)):
    try:
        return await service.refresh(data.refresh_token)
    except InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=str(e))
    

@router.post(
    "/register",
    response_model=TokenPair,
    status_code=201,
    dependencies=[Depends(login_rate_limit)],
)
async def register(data: OrganizationBootstrap, service: AuthService = Depends(get_auth_service)):
    try:
        return await service.register(data)
    except DuplicateTaxIdError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except UserAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))