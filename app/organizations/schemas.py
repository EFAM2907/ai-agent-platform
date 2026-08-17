from pydantic import BaseModel
from datetime import datetime

class OrganizationCreate(BaseModel):
    name: str
    tax_id: str | None = None
    
    
class OrganizationResponse(BaseModel):
    name: str
    tax_id: str | None = None
    plan_type: str = "free"
    created_at : datetime
    
    model_config = {"from_attributes": True}
    
