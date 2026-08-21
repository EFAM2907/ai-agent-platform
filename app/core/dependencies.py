from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_access_token
from app.core.exceptions import InvalidTokenError
from app.users.repository import UserRepository
from app.users.models import User, UserRole
from uuid import UUID

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
    
    # Verificar si el usuario no existe o si fue borrado lógicamente
    if user is None or user.deleted_at is not None:
        raise HTTPException(status_code=401, detail="User does not exist or has been deleted")
        
    return user



async def require_admin(
    current_user: User = Depends(get_current_user)
)-> User:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return current_user