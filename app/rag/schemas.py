"""Schemas REST de la base de conocimiento (app.rag.api)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class KBDocumentResponse(BaseModel):
    id: uuid.UUID
    title: str
    source_type: str
    source_ref: str | None = None
    mime_type: str | None = None
    access_level: str
    chunk_count: int
    indexed_at: datetime | None = None
    source_modified_at: datetime | None = None
    updated_at: datetime


class KBDocumentListResponse(BaseModel):
    total: int
    returned: int
    documents: list[KBDocumentResponse]


class KBStatsResponse(BaseModel):
    articles: int
    chunks: int
    by_source: dict[str, int]
    supported_formats: list[str]


class KBUploadResult(BaseModel):
    id: uuid.UUID
    title: str
    chunk_count: int
    # created: documento nuevo; updated: cambio el contenido y se reindexo;
    # unchanged: mismo contenido, no se toco el proveedor de embeddings.
    status: str


class KBUploadResponse(BaseModel):
    filename: str
    documents: list[KBUploadResult]


class KBSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    top_k: int = Field(default=5, ge=1, le=10)


class KBSearchHit(BaseModel):
    article_id: uuid.UUID
    title: str
    chunk_index: int
    score: float
    snippet: str


class KBSearchResponse(BaseModel):
    query: str
    # Lista vacia = la KB no tiene nada relevante (filtro de relevancia),
    # no un error: ver RAGService.retrieve.
    results: list[KBSearchHit]
