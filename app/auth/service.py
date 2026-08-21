from app.core.security import verify_password, create_access_token
from app.core.exceptions import InvalidCredentialsError
from app.users.repository import UserRepository
from app.auth.schemas import LoginRequest

class AuthService:
    def __init__(self, repository: UserRepository):
        self.repository = repository

    async def login(self, credentials: LoginRequest) -> str:
        user = await self.repository.get_by_email(credentials.email)
        if user is None or user.deleted_at is not None:
            raise InvalidCredentialsError("Invalid email or password")

        verify = verify_password(plain_password=credentials.password, hashed_password=user.hashed_password)
        if not verify:
            raise InvalidCredentialsError("Invalid email or password")

        token = create_access_token({
            "sub": str(user.id),
            "organization_id": str(user.organization_id)
        })
        return token
        
        