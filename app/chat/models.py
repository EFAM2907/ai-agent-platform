import enum
import uuid
from datetime import datetime

from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    title: Mapped[str | None] = mapped_column(String(200), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE")
    )
    # Denormalizado igual que kb_chunks.organization_id -- mismo
    # motivo: cualquier query de este dominio filtra por
    # organization_id sin depender de un join a chat_sessions.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    role: Mapped[MessageRole] = mapped_column()
    content: Mapped[str] = mapped_column(Text)
    # Titulos de los articulos de KB usados para responder, separados
    # por "|" -- solo se llena en mensajes ASSISTANT que encontraron
    # contexto relevante. Una tabla de citas propia hubiera sido
    # sobre-ingenieria para lo que el review necesita mostrar (que la
    # respuesta cita su fuente), no para reporting real todavia.
    sources: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Valoracion del usuario sobre una respuesta del asistente: 1 (util) o
    # -1 (no util). Sirve para encontrar las respuestas que fallan de verdad.
    feedback: Mapped[int | None] = mapped_column(SmallInteger, nullable=True, default=None)
    feedback_comment: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    feedback_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    session: Mapped["ChatSession"] = relationship(back_populates="messages")


class ChatTurnTrace(Base):
    """Traza de UN turno de chat (ver app.chat.tracing.TurnTracer). Los
    JSONB guardan listas/dicts chicos y de forma estable; lo que se
    filtra o agrega en SQL (estado, modelo, tokens, costo, latencia) son
    columnas propias."""

    __tablename__ = "chat_turn_traces"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE")
    )
    user_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True
    )
    assistant_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True
    )
    # Mismo id que se manda a Langfuse (via LiteLLM) para todas las
    # llamadas del turno.
    trace_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(10))  # "ok" | "error"
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True, default=None)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True, default=None)
    route: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    retrieval: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    llm_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # Delegacion (RFC 8693) de este turno: actor (chat-service), sujeto
    # (usuario), jti del token delegado y scopes concedidos.
    delegation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True, default=None)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PendingActionStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"  # aprobada, ejecutandose
    EXECUTED = "executed"
    FAILED = "failed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PendingAction(Base):
    """Accion destructiva que el agente pidio ejecutar y que espera la
    aprobacion explicita del usuario (human-in-the-loop tecnico, no solo
    una regla del prompt). El MCP server rechaza estas tools sin un
    token que lleve la aprobacion de esta accion (ver mcp_server/auth.py)."""

    __tablename__ = "chat_pending_actions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="SET NULL"), nullable=True
    )
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    tool_name: Mapped[str] = mapped_column(String(100))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(10), default=PendingActionStatus.PENDING.value)
    result: Mapped[Any] = mapped_column(JSONB, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
