import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.chat.models import MessageRole


class ChatMessageIn(BaseModel):
    session_id: uuid.UUID | None = None
    message: str


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: MessageRole
    content: str
    sources: list[str] = []
    created_at: datetime
    # 1 / -1 / None: valoracion del usuario sobre esta respuesta.
    feedback: int | None = None


class ChatReplyOut(BaseModel):
    session_id: uuid.UUID
    message: ChatMessageOut
    trace_id: str | None = None
    pending_actions: list[dict] = []


class ChatSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    created_at: datetime
    messages: list[ChatMessageOut]


class ChatSessionRenameIn(BaseModel):
    """Body de PATCH /chat/sessions/{id} -- min_length=1 solo evita el
    caso trivial de mandar "" sin espacios; el trim de espacios reales
    ("   ") se hace en el endpoint, porque Pydantic no lo hace solo."""

    title: str = Field(min_length=1, max_length=200)


class ChatSessionSummaryOut(BaseModel):
    """Version liviana de ChatSessionOut para el listado del historial
    -- sin `messages`, para no traer conversaciones enteras solo para
    pintar la lista del sidebar."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    created_at: datetime


# ---------------------------------------------------------------------
# Trazabilidad y feedback
# ---------------------------------------------------------------------
class TraceToolCallOut(BaseModel):
    name: str
    arguments: dict = {}
    ok: bool
    duration_ms: float
    result_chars: int = 0
    error: str | None = None


class TraceLLMCallOut(BaseModel):
    purpose: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float | None = None
    finish_reason: str


class TraceSummaryOut(BaseModel):
    """Una fila del listado de trazas (vista de administracion)."""

    id: uuid.UUID
    trace_id: str
    created_at: datetime
    user_id: uuid.UUID
    session_id: uuid.UUID
    status: str
    error_type: str | None = None
    model: str | None = None
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None
    latency_ms: int
    tools_used: int = 0
    kb_chunks: int = 0
    route_reason: str | None = None


class TraceListOut(BaseModel):
    total: int
    returned: int
    traces: list[TraceSummaryOut]


class TraceDetailOut(BaseModel):
    id: uuid.UUID
    trace_id: str
    created_at: datetime
    user_id: uuid.UUID
    session_id: uuid.UUID
    user_message_id: uuid.UUID | None = None
    assistant_message_id: uuid.UUID | None = None
    status: str
    error_type: str | None = None
    error_message: str | None = None
    model: str | None = None
    route: dict = {}
    retrieval: dict = {}
    llm_calls: list[TraceLLMCallOut] = []
    tool_calls: list[TraceToolCallOut] = []
    delegation: dict = {}
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None
    latency_ms: int


class TraceStatsOut(BaseModel):
    days: int
    turns: int
    errors: int
    avg_latency_ms: int
    p95_latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None
    by_model: list[dict]
    by_tool: list[dict]
    feedback_positive: int
    feedback_negative: int


class FeedbackIn(BaseModel):
    """rating: 1 (util), -1 (no util) o null para retirar la valoracion."""

    rating: int | None = Field(default=None)
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackOut(BaseModel):
    message_id: uuid.UUID
    rating: int | None = None
    comment: str | None = None


class PendingActionOut(BaseModel):
    """Accion destructiva que el agente pidio y espera tu aprobacion."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool_name: str
    arguments: dict
    status: str
    result: Any = None
    created_at: datetime
    expires_at: datetime
