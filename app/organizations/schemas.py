from pydantic import BaseModel, EmailStr
from datetime import datetime
import uuid

class OrganizationCreate(BaseModel):
    name: str
    tax_id: str | None = None


class OrganizationBootstrap(OrganizationCreate):
    """Initial registration data for an organization and its first OWNER."""
    owner_email: str
    owner_password: str
    owner_full_name: str
    
    
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
    

# app/organizations/schemas.py

class OrganizationBootstrap(BaseModel):
    name: str
    tax_id: str 
    owner_email: EmailStr
    owner_password: str
    owner_full_name: str