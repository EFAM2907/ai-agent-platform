"""Loader de PDF via pypdf -- extraccion de texto pagina por pagina,
sin OCR. Un PDF escaneado (imagen sin capa de texto) no tiene nada que
extraer de esta forma; DocumentLoadError es la senal explicita de eso,
no un texto vacio silencioso que despues generaria un articulo sin
chunks en la KB. Agregar OCR (o mandar las paginas a un modelo
multimodal, que hoy `app.llm.schemas.Message` ni siquiera soporta --
content es un string plano) queda fuera de este primer paso, junto con
imagenes -- ver docs/roadmap.md seccion 3."""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.rag.ingestion.base import DocumentLoadError, IngestedDocument


class PDFLoader:
    def load(self, path: Path) -> list[IngestedDocument]:
        try:
            reader = PdfReader(str(path))
            pages = [(page.extract_text() or "").strip() for page in reader.pages]
        except PdfReadError as exc:
            raise DocumentLoadError(
                f"{path.name}: PDF invalido o corrupto ({exc})"
            ) from exc

        content = "\n\n".join(page for page in pages if page)
        if not content:
            raise DocumentLoadError(
                f"{path.name}: no se pudo extraer texto (¿PDF escaneado sin "
                "capa de texto? necesita OCR, no soportado todavia)"
            )
        return [IngestedDocument(title=path.stem, content=content)]
