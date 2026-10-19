"""Contrato compartido para los loaders de fuentes de la KB.

Cada loader sabe leer UN formato de archivo (markdown, PDF, Excel) y
normalizarlo a la misma forma -- una lista de `IngestedDocument` -- para
que el resto del pipeline (chunking, embeddings,
`RAGService.ingest_article`) nunca tenga que saber de donde vino el
contenido. Un solo archivo puede producir mas de un documento (ej. un
Excel con varias hojas -> un IngestedDocument por hoja), por eso
`load()` devuelve una lista y no un unico documento.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass
class IngestedDocument:
    """title/content: igual que los dos primeros argumentos de
    `RAGService.ingest_article` hoy -- cualquier loader nuevo se integra
    sin tocar esa firma.

    chunks: override opcional del chunking por defecto (ventanas de
    palabras, pensado para prosa). None (el caso normal: markdown, PDF)
    dice "usa el chunking por defecto". Una fuente estructurada como
    Excel lo setea explicito -- partir una tabla en ventanas de 150
    palabras corta filas a la mitad sin ningun sentido, asi que ahi el
    loader arma un chunk por fila en vez de dejar que chunk_text() la
    trate como prosa. Ver ExcelLoader."""

    title: str
    content: str
    chunks: list[str] | None = None
    # De donde salio (lo completa el registry / el conector, no el loader):
    # nombre de archivo, id de Drive... Ver KBArticle.source_ref.
    source_ref: str | None = None
    mime_type: str | None = None
    source_modified_at: datetime | None = None


class DocumentLoadError(Exception):
    """Un archivo de un formato soportado no se pudo convertir a texto
    -- ej. un PDF escaneado sin capa de texto, o un PDF corrupto. No es
    un bug del loader, es una propiedad del archivo de entrada; quien
    llama decide si eso aborta todo o solo salta ese archivo (ver
    registry.load_documents_from_dir, que hace lo segundo)."""


class Loader(Protocol):
    def load(self, path: Path) -> list[IngestedDocument]: ...
