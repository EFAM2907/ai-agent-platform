"""Punto unico de entrada del pipeline de ingestion: dado un archivo o
una carpeta, elige el loader por extension y devuelve documentos ya
normalizados (ver base.IngestedDocument). Agregar un formato nuevo es
sumar una entrada a _LOADERS, no tocar nada que ya use esto (el script
de ingesta, o lo que lo reemplace mas adelante)."""

from __future__ import annotations

import logging
from pathlib import Path

from app.rag.ingestion.base import DocumentLoadError, IngestedDocument, Loader
from app.rag.ingestion.excel_loader import ExcelLoader
from app.rag.ingestion.markdown_loader import MarkdownLoader
from app.rag.ingestion.pdf_loader import PDFLoader

logger = logging.getLogger(__name__)

_LOADERS: dict[str, Loader] = {
    ".md": MarkdownLoader(),
    ".pdf": PDFLoader(),
    ".xlsx": ExcelLoader(),
}


class UnsupportedSourceError(Exception):
    """Extension sin loader registrado. A diferencia de DocumentLoadError
    (el archivo es del formato correcto pero no se pudo leer), esto es
    "no sabemos leer este formato todavia" -- load_documents_from_dir lo
    trata distinto: un archivo de un formato no soportado se saltea con
    un warning (puede ser un README suelto en la carpeta), no aborta el
    resto de la ingesta. load_documents (un solo archivo, elegido a
    proposito) si lo deja propagarse."""


def load_documents(path: Path) -> list[IngestedDocument]:
    """Un solo archivo -> sus documentos. Deja que DocumentLoadError se
    propague -- quien llama decide si eso aborta o se saltea (ver
    load_documents_from_dir para la politica que usa el pipeline)."""
    loader = _LOADERS.get(path.suffix.lower())
    if loader is None:
        raise UnsupportedSourceError(
            f"{path.name}: extension '{path.suffix}' sin loader registrado "
            f"(soportadas: {', '.join(sorted(_LOADERS))})"
        )
    return loader.load(path)


def load_documents_from_dir(directory: Path) -> list[IngestedDocument]:
    """Todos los archivos de formato soportado en `directory` (no
    recursivo), en orden alfabetico -- mismo orden predecible que ya
    tenia `sorted(articles_dir.glob("*.md"))` en el script original.

    Un archivo de extension no soportada, o que falla al leerse (ej. un
    PDF escaneado sin capa de texto), se saltea con un warning en vez de
    abortar toda la ingesta -- una carpeta de KB real va a tener
    archivos sueltos que no son fuente (un README, un .gitkeep), y un
    solo PDF malo no deberia tumbar la ingesta de los otros archivos
    buenos."""
    documents: list[IngestedDocument] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _LOADERS:
            logger.warning("Saltando %s: extension no soportada", path.name)
            continue
        try:
            documents.extend(load_documents(path))
        except DocumentLoadError as exc:
            logger.warning("Saltando %s: %s", path.name, exc)
    return documents
