from sqlalchemy.ext.asyncio import AsyncSession
from app.users.models import User
from app.users.schemas import UserCreate
from sqlalchemy import select
from datetime import datetime, timezone
import uuid

class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session
        
    async def create(self, data: dict) -> User:
        user = User(
            email=data["email"],
            hashed_password=data["hashed_password"],
            full_name=data["full_name"],
            organization_id=data["organization_id"]
        )
        self.session.add(user)
        await self.session.flush()
        return user
            
    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        stmt = select(User).where(User.id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
        
        
    async def get_by_email(self, email) -> User | None:
        stmt = select(User).where(User.email == email)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
        
        
    async def list(self,organization_id, skip: int = 0, limit: int = 50) -> list[User]:
        result = await self.session.execute(
            select(User)
            .where(
                User.deleted_at.is_(None),
                User.organization_id == organization_id
                )
            .offset(skip)
            .limit(limit)
        )

        return result.scalars().all() 
    
    async def update(self, user: User, data: dict) -> User:
        for key, value in data.items():
            setattr(user, key, value)
        await self.session.flush()
        return user
    
    async def delete(self, user: User) -> User:
        user.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()
        return user
            