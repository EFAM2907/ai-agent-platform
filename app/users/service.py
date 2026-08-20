from sqlalchemy.ext.asyncio import AsyncSession
from app.users.repository import UserRepository
from app.users.schemas import UserCreate, UserUpdate
from app.users.models import User
from app.core.security import hash_password
from app.users.exceptions import UserAlreadyExistsError, UserNotFoundError
import uuid

class UserService:
    def __init__(self, repository: UserRepository, session: AsyncSession):
        self.repository = repository
        self.session = session
        
    async def create(self, data: UserCreate) -> User:
        if data.email:
            user = await self.repository.get_by_email(data.email)
            if user:
                raise UserAlreadyExistsError(data.email)
        data_dict = data.model_dump()
        if "password" in data_dict:
             plain_password = data_dict.pop("password")
             data_dict["hashed_password"] = hash_password(plain_password)
        new_user = await self.repository.create(data_dict)
        await self.session.commit()
        return new_user

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self.repository.get_by_id(user_id)

    async def list(self, skip: int = 0, limit: int = 50) -> list[User]:
        return await self.repository.list(skip, limit)

    async def update(self, user_id: uuid.UUID, data: UserUpdate) -> User:
        user = await self.repository.get_by_id(user_id)
        if not user:
            raise UserNotFoundError(str(user_id))
        
        update_data = data.model_dump(exclude_unset=True)
        if "password" in update_data:
            update_data["hashed_password"] = update_data.pop("password")
            
        updated_user = await self.repository.update(user, update_data)
        await self.session.commit()
        return updated_user

    async def delete(self, user_id: uuid.UUID) -> User:
        user = await self.repository.get_by_id(user_id)
        if not user:
            raise UserNotFoundError(str(user_id))
        
        deleted_user = await self.repository.delete(user)
        await self.session.commit()
        return deleted_user