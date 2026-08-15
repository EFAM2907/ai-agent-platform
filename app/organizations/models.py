from sqlalchemy import String, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base 
from datetime import datetime




class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    tax_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    plan_type: Mapped[str] = mapped_column(String(50), default="free")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())