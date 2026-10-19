from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ClaimOpen(BaseModel):
    tracking_number: str = Field(min_length=1, max_length=50)
    # str y no ClaimType a proposito (mismo criterio que
    # ShipmentStatusUpdate): el endpoint responde un 400 con la lista de
    # tipos validos en vez de un 422 generico de enum.
    claim_type: str
    description: str = Field(min_length=10, max_length=2000)


class ClaimStatusUpdate(BaseModel):
    new_status: str
    note: str | None = Field(default=None, max_length=2000)


class ClaimEventResponse(BaseModel):
    status: str
    note: str | None = None
    user_id: uuid.UUID
    created_at: datetime


class ClaimResponse(BaseModel):
    claim_number: str
    tracking_number: str
    customer_name: str
    claim_type: str
    rec_code: str
    status: str
    description: str
    resolution: str | None = None
    sla_due_at: datetime
    sla_overdue: bool
    opened_by_user_id: uuid.UUID
    created_at: datetime
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    history: list[ClaimEventResponse] = []


class ClaimListResponse(BaseModel):
    total: int
    returned: int
    claims: list[ClaimResponse]
