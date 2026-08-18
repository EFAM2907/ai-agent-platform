import datetime
from pydantic import BaseModel
from app.users.models import UserRole
import uuid

class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str
    organization_id: uuid.UUID
    
class UserUpdate(BaseModel):
    email: str | None = None
    full_name: str | None = None
    password: str | None = None

class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    organization_id: uuid.UUID
    created_at: datetime
    
    model_config = {"from_attributes": True}