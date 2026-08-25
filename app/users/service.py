from sqlalchemy.ext.asyncio import AsyncSession
from app.users.repository import UserRepository
from app.users.schemas import UserCreate, UserUpdate
from app.users.models import User, UserRole
from app.core.security import hash_password
from app.users.exceptions import UserAlreadyExistsError, UserNotFoundError
import uuid

class UserService:
    def __init__(self, repository: UserRepository, session: AsyncSession):
        self.repository = repository
        self.session = session
        
    async def create(self, admin: User, data: UserCreate) -> User:
        if data.email:
            user = await self.repository.get_by_email(data.email)
            if user:
                raise UserAlreadyExistsError(data.email)
        data_dict = data.model_dump()
        if "password" in data_dict:
             plain_password = data_dict.pop("password")
             data_dict["hashed_password"] = hash_password(plain_password)
        new_user = await self.repository.create(admin,data_dict)
        await self.session.commit()
        return new_user

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self.repository.get_by_id(user_id)

    async def list(self, organization_id: uuid.UUID, skip: int = 0, limit: int = 50) -> list[User]:
        return await self.repository.list(organization_id, skip, limit)

    async def update(self, user_id: uuid.UUID, data: UserUpdate) -> User:
        user = await self.repository.get_by_id(user_id)
        if not user:
            raise UserNotFoundError(str(user_id))
        
        update_data = data.model_dump(exclude_unset=True)
        if "password" in update_data:
            update_data["hashed_password"] = hash_password(update_data.pop("password"))
            
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
    
    async def update_role(self, user_id: uuid.UUID, new_role: UserRole) -> User:
        user = await self.repository.get_by_id(user_id)
        if user is None or user.deleted_at is not None:
            raise UserNotFoundError(f"User {user_id} not found")

        updated_user = await self.repository.update(user, {"role": new_role})
        await self.session.commit()
        return updated_user

    async def transfer_ownership(self, current_owner_id: uuid.UUID, target_user_id: uuid.UUID) -> User:
        """Swap ownership in one transaction while both rows are locked."""
        current_owner = await self.repository.get_by_id(current_owner_id, for_update=True)
        target = await self.repository.get_by_id(target_user_id, for_update=True)
        if current_owner is None or current_owner.deleted_at is not None:
            raise UserNotFoundError(f"User {current_owner_id} not found")
        if target is None or target.deleted_at is not None:
            raise UserNotFoundError(f"User {target_user_id} not found")
        if current_owner.organization_id != target.organization_id:
            raise ValueError("Target user must belong to the same organization")
        if target.id == current_owner.id:
            raise ValueError("Cannot transfer ownership to yourself")
        if current_owner.role != UserRole.OWNER:
            raise PermissionError("Only the current owner can transfer ownership")

        # These changes share the request transaction and are committed together.
        # Demote first so the partial unique OWNER index remains valid.
        await self.repository.update(current_owner, {"role": UserRole.ADMIN})
        await self.repository.update(target, {"role": UserRole.OWNER})
        await self.session.commit()
        return target
