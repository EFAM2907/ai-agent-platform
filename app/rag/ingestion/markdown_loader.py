"""Loader de markdown -- el mismo parsing que ya usaba
scripts/ingest_kb_articles.py antes de que existiera este paquete,
movido aca sin cambiar el comportamiento: la primera linea '# Titulo'
se vuelve el titulo del articulo, el resto del archivo es el
contenido. Sin esa linea, el titulo cae al nombre del archivo."""

from __future__ import annotations

from pathlib import Path

from app.rag.ingestion.base import IngestedDocument


class MarkdownLoader:
    def load(self, path: Path) -> list[IngestedDocument]:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if lines and lines[0].startswith("# "):
            title = lines[0][2:].strip()
            content = "\n".join(lines[1:]).strip()
        else:
            title = path.stem
            content = text.strip()
        return [IngestedDocument(title=title, content=content)]
