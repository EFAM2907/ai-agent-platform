from pydantic import BaseModel, EmailStr
from datetime import datetime
import uuid

class OrganizationCreate(BaseModel):
    name: str
    tax_id: str | None = None


class OrganizationResponse(BaseModel):
    id: uuid.UUID
    name: str
    tax_id: str | None = None
    plan_type: str = "free"
    created_at : datetime
    
    model_config = {"from_attributes": True}
    
class OrganizationUpdate(BaseModel):
    
    name: str | None = None
    tax_id: str | None = None
    plan_type: str | None = None


class OrganizationBootstrap(BaseModel):
    """Initial registration data for an organization and its first OWNER."""
    name: str
    tax_id: str
    owner_email: EmailStr
    owner_password: str
    owner_full_name: str