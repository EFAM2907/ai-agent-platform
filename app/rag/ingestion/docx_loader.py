"""Loader de Word (.docx) via python-docx -- un IngestedDocument por
archivo, igual que PDFLoader.

El documento se recorre en el orden real del cuerpo (parrafos y tablas
intercalados), no "primero todos los parrafos y despues todas las
tablas": una tabla se entiende por el titulo que la precede.

Dos tipos de contenido, dos tipos de chunk (mismo criterio que
ExcelLoader):
- La prosa (titulos, parrafos, listas) se convierte a markdown y se
  chunkea con ventanas de palabras (chunk_text), como un .md.
- Cada fila de una tabla es un chunk propio, "Seccion > columna: valor;
  ...", con el ultimo titulo visto como prefijo -- partir una tabla en
  ventanas de 150 palabras corta filas a la mitad, y una fila suelta sin
  su seccion ("Codigo: P-14; Descripcion: ...") no dice de que lista
  viene.

`content` (kb_articles.content) conserva el documento completo, con las
tablas como markdown. Formatos legacy (.doc) no estan soportados --
python-docx solo lee .docx. Las imagenes y los cuadros de texto no se
extraen (igual que el PDF sin OCR)."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.rag.chunking import chunk_text
from app.rag.ingestion.base import DocumentLoadError, IngestedDocument

_HEADING_STYLE = re.compile(r"^heading\s*(\d+)$", re.IGNORECASE)


def _heading_level(paragraph: Paragraph) -> int | None:
    style = (paragraph.style.name if paragraph.style is not None else "") or ""
    if style.lower() == "title":
        return 1
    match = _HEADING_STYLE.match(style)
    return int(match.group(1)) if match else None


def _is_list_item(paragraph: Paragraph) -> bool:
    style = (paragraph.style.name if paragraph.style is not None else "") or ""
    return style.lower().startswith("list")


def _clean(value: str) -> str:
    return " ".join(value.split())


class DocxLoader:
    def load(self, path: Path) -> list[IngestedDocument]:
        try:
            document = Document(str(path))
        except (PackageNotFoundError, zipfile.BadZipFile, KeyError) as exc:
            raise DocumentLoadError(
                f"{path.name}: DOCX invalido o corrupto ({exc})"
            ) from exc

        prose_lines: list[str] = []  # solo prosa -> chunk_text
        full_lines: list[str] = []  # documento completo -> content
        row_chunks: list[str] = []
        current_heading = ""

        for child in document.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]

            if tag == "p":
                paragraph = Paragraph(child, document)
                text = _clean(paragraph.text)
                if not text:
                    continue
                level = _heading_level(paragraph)
                if level is not None:
                    current_heading = text
                    line = f"{'#' * level} {text}"
                elif _is_list_item(paragraph):
                    line = f"- {text}"
                else:
                    line = text
                prose_lines.append(line)
                full_lines.append(line)

            elif tag == "tbl":
                table_chunks, table_lines = self._read_table(
                    Table(child, document), current_heading
                )
                row_chunks.extend(table_chunks)
                full_lines.extend(table_lines)

        content = "\n\n".join(line for line in full_lines if line).strip()
        if not content:
            raise DocumentLoadError(f"{path.name}: el documento Word no tiene texto")

        prose = "\n\n".join(prose_lines).strip()
        chunks = (chunk_text(prose) if prose else []) + row_chunks
        return [IngestedDocument(title=path.stem, content=content, chunks=chunks)]

    @staticmethod
    def _read_table(table: Table, heading: str) -> tuple[list[str], list[str]]:
        rows = [[_clean(cell.text) for cell in row.cells] for row in table.rows]
        rows = [row for row in rows if any(row)]
        if not rows:
            return [], []

        header_row, *data_rows = rows
        headers = [h or f"columna_{i + 1}" for i, h in enumerate(header_row)]

        lines = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
        ]
        chunks: list[str] = []
        prefix = f"{heading} > " if heading else ""
        for row in data_rows:
            lines.append("| " + " | ".join(row) + " |")
            pairs = [f"{h}: {v}" for h, v in zip(headers, row) if v]
            if pairs:
                chunks.append(prefix + "; ".join(pairs))
        return chunks, ["\n".join(lines)]
