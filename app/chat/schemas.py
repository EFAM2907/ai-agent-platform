import uuid
from datetime import datetime

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


class ChatReplyOut(BaseModel):
    session_id: uuid.UUID
    message: ChatMessageOut


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
