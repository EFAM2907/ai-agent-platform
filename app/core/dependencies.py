from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_access_token
from app.core.exceptions import InvalidTokenError
from app.users.repository import UserRepository
from app.users.models import User, UserRole, ROLE_HIERARCHY
from uuid import UUID
import uuid

security = HTTPBearer()


async def get_current_user(
    auth: HTTPAuthorizationCredentials = Depends(security),
    session: AsyncSession = Depends(get_db)
) -> User:
    token = auth.credentials
    try:
        dec_token = decode_access_token(token)
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
        
    sub = dec_token.get('sub')
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token payload")
        
    try:
        user_uuid = UUID(sub)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format in token")

    repository = UserRepository(session)
    user = await repository.get_by_id(user_uuid)
    
    # Verifica si el usuario no existe o si fue borrado lógicamente
    if user is None or user.deleted_at is not None:
        raise HTTPException(status_code=401, detail="User does not exist or has been deleted")
        
    return user



async def require_admin(
    current_user: User = Depends(get_current_user)
)-> User:
    if current_user.role not in (UserRole.ADMIN, UserRole.OWNER):
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return current_user


def verify_same_organization(admin: User, target_organization_id: uuid.UUID) -> None:
    # Platform support access is an explicit, read-only endpoint decision.
    if admin.is_platform_admin:
        return
    if admin.organization_id != target_organization_id:
        raise HTTPException(status_code=404, detail="User not found")


def require_owner(current_user: User) -> User:
    if current_user.role != UserRole.OWNER:
        raise HTTPException(status_code=403, detail="Owner privileges required")
    return current_user


def can_manage_other_user(actor: User, target: User) -> bool:
    """Organization role hierarchy; platform admin never grants write access here."""
    return ROLE_HIERARCHY[actor.role] > ROLE_HIERARCHY[target.role]
