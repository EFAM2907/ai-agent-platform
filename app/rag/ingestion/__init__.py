"""Pipeline de ingestion de la KB: loaders por formato (markdown, PDF,
Excel) que normalizan cualquier fuente a IngestedDocument, mas el
registry que elige el loader correcto por extension y por carpeta. Ver
docs/roadmap.md seccion 3 para el contexto de por que existe esto."""

from app.rag.ingestion.base import DocumentLoadError, IngestedDocument
from app.rag.ingestion.registry import (
    UnsupportedSourceError,
    load_documents,
    load_documents_from_dir,
)

__all__ = [
    "DocumentLoadError",
    "IngestedDocument",
    "UnsupportedSourceError",
    "load_documents",
    "load_documents_from_dir",
]
