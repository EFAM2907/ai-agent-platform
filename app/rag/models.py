import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    pass

# Debe coincidir con output_dimensionality en app.rag.embeddings y con
# EMBEDDING_DIM en la migracion e1a2b3c4d5f6_add_rag_kb_tables -- los
# tres lugares tienen que cambiar juntos si el modelo de embeddings
# cambia algun dia.
EMBEDDING_DIM = 768

# Configuracion de texto de Postgres para la busqueda por palabras clave.
# Debe coincidir con la migracion d2e5f8a1b3c4_add_fulltext_search_to_kb_chunks.
FTS_CONFIG = "spanish"

# Valores de KBArticle.source_type.
SOURCE_MANUAL = "manual"  # creado a mano / desde codigo
SOURCE_FILE = "file"  # carpeta local kb_content/ (scripts de ingesta)
SOURCE_UPLOAD = "upload"  # subido por la API (POST /kb/documents)
SOURCE_DRIVE = "drive"  # sincronizado desde Google Drive
SOURCE_TYPES = (SOURCE_MANUAL, SOURCE_FILE, SOURCE_UPLOAD, SOURCE_DRIVE)

# Valores de KBArticle.access_level, de menor a mayor privilegio; coinciden
# con los valores de app.users.models.UserRole.
ACCESS_LEVELS = ("viewer", "member", "admin", "owner")


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

    # --- Origen y version (migracion e3f6a9b2c5d7) -------------------------
    # De donde salio el documento y como volver a encontrarlo en su fuente:
    # la ruta de archivo, el id de Drive, el nombre del archivo subido. Es
    # lo que permite sincronizar una fuente (actualizar lo que cambio,
    # retirar lo que desaparecio) sin reglas especiales por conector.
    source_type: Mapped[str] = mapped_column(String(20), default=SOURCE_MANUAL)
    source_ref: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True, default=None)
    # Nivel de rol minimo para que este documento pueda llegar al contexto
    # de una conversacion: "viewer" = cualquiera de la organizacion.
    access_level: Mapped[str] = mapped_column(String(10), default="viewer")
    # SHA-256 hex del contenido: si no cambia (y el modelo de embeddings
    # tampoco) no hay que volver a chunkear ni a embeber.
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True, default=None)
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    source_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
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
    # Columna generada por Postgres a partir de `content` (ver la
    # migracion d2e5f8a1b3c4) -- nunca se escribe desde Python, solo se
    # consulta para la parte de palabras clave de la busqueda hibrida.
    content_tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(f"to_tsvector('{FTS_CONFIG}', content)", persisted=True),
        deferred=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    article: Mapped["KBArticle"] = relationship(back_populates="chunks")
