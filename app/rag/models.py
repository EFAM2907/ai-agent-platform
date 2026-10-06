import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    pass

# Debe coincidir con output_dimensionality en app.rag.embeddings y con
# EMBEDDING_DIM en la migracion e1a2b3c4d5f6_add_rag_kb_tables -- los
# tres lugares tienen que cambiar juntos si el modelo de embeddings
# cambia algun dia.
EMBEDDING_DIM = 768


class KBArticle(Base):
    __tablename__ = "kb_articles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    chunks: Mapped[list["KBChunk"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )


class KBChunk(Base):
    __tablename__ = "kb_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kb_articles.id", ondelete="CASCADE")
    )
    # Denormalizado a proposito -- retrieval filtra por organization_id
    # directo en esta tabla, sin depender de un join a kb_articles. Ver
    # el comentario en la migracion para el porque (defensa en
    # profundidad contra un leak cross-tenant).
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    article: Mapped["KBArticle"] = relationship(back_populates="chunks")
