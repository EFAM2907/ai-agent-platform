from app.core.security import verify_password, create_access_token, generate_refresh_token,hash_refresh_token, hash_password
from app.core.exceptions import InvalidCredentialsError,InvalidTokenError
from app.users.exceptions import UserAlreadyExistsError
from app.organizations.exceptions import DuplicateTaxIdError
from app.organizations.schemas import OrganizationCreate
from app.users.repository import UserRepository
from app.auth.schemas import LoginRequest
from datetime import datetime, timedelta, timezone
from app.auth.repository import RefreshTokenRepository
from app.users.repository import UserRepository
from app.organizations.repository import OrganizationRepository
from app.organizations.schemas import OrganizationBootstrap
from app.users.models import User, UserRole


REFRESH_TOKEN_EXPIRE_DAYS = 30

class AuthService:
    def __init__(self, repository: UserRepository, refresh_repository: RefreshTokenRepository, organization_repository: OrganizationRepository, session):
        self.repository = repository
        self.refresh_repository = refresh_repository
        self.organization_repository = organization_repository
        self.session = session



    async def register(self, data: OrganizationBootstrap) -> dict:
        existing_org = await self.organization_repository.get_by_tax_id(data.tax_id)
        if existing_org is not None:
            raise DuplicateTaxIdError(data.tax_id)

        existing_user = await self.repository.get_by_email(data.owner_email)
        if existing_user is not None:
            raise UserAlreadyExistsError("A user with this email already exists")

        organization = await self.organization_repository.create(
            OrganizationCreate(name=data.name, tax_id=data.tax_id)
        )

        owner = User(
            email= data.owner_email,
            hashed_password= hash_password(data.owner_password),
            full_name=data.owner_full_name,
            organization_id= organization.id,
            role= UserRole.OWNER,
        )
        self.session.add(owner)
        await self.session.flush()

        return await self._issue_token_pair(owner)
    async def login(self, credentials: LoginRequest) -> dict:
        user = await self.repository.get_by_email(credentials.email)
        if user is None or user.deleted_at is not None:
            raise InvalidCredentialsError("Invalid email or password")

        verify = verify_password(plain_password=credentials.password, hashed_password=user.hashed_password)
        if not verify:
            raise InvalidCredentialsError("Invalid email or password")

        return await self._issue_token_pair(user)

    async def refresh(self, raw_refresh_token: str) -> dict:
        token_hash = hash_refresh_token(raw_refresh_token)
        stored_token = await self.refresh_repository.get_by_hash(token_hash)

        if stored_token is None:
            raise InvalidTokenError("Invalid refresh token")
        if stored_token.revoked_at is not None:
            raise InvalidTokenError("Refresh token has been revoked")
        if stored_token.expires_at < datetime.now(timezone.utc):
            raise InvalidTokenError("Refresh token has expired")

        user = await self.repository.get_by_id(stored_token.user_id)
        if user is None or user.deleted_at is not None:
            raise InvalidTokenError("User no longer exists")

        await self.refresh_repository.revoke(stored_token)
        return await self._issue_token_pair(user)

    async def _issue_token_pair(self, user) -> dict:
        access_token = create_access_token({
            "sub": str(user.id),
            "organization_id": str(user.organization_id),
        })

        raw_refresh_token = generate_refresh_token()
        token_hash = hash_refresh_token(raw_refresh_token)
        expires_at = datetime.now(timezone.utc) + timedelta(days=30)

        await self.refresh_repository.create(user.id, token_hash, expires_at)
        await self.session.commit()

        return {
            "access_token": access_token,
            "refresh_token": raw_refresh_token,
            "token_type": "bearer",
        }
        
        