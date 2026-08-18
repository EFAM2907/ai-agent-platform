from pydantic import BaseModel
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
    
