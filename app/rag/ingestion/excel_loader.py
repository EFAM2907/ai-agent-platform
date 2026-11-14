"""Loader de Excel (.xlsx) via openpyxl -- una IngestedDocument por
hoja no vacia. No usa chunk_text() (ventanas de palabras): una fila de
tabla no es prosa, y cortarla a la mitad porque cayo en el borde de una
ventana de 150 palabras no tiene ningun sentido semantico. En vez de
eso, cada IngestedDocument trae su propio `chunks` -- un chunk por
fila, como "columna: valor; columna: valor" -- que
RAGService.ingest_article usa tal cual en vez de re-chunkear con la
logica de prosa (ver el parametro `chunks` en ese metodo).

`content` (lo que se guarda en kb_articles.content) sigue siendo la
hoja completa como tabla markdown -- se conserva el documento original
completo aunque el chunking sea por fila.

Formatos legacy (.xls, BIFF) no estan soportados -- openpyxl solo lee
.xlsx/.xlsm. No es una limitacion que valga la pena resolver ahora."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from app.rag.ingestion.base import IngestedDocument


def _is_blank_row(row: tuple[Any, ...]) -> bool:
    return not any(cell is not None and str(cell).strip() for cell in row)


class ExcelLoader:
    def load(self, path: Path) -> list[IngestedDocument]:
        workbook = load_workbook(str(path), data_only=True, read_only=True)
        try:
            return self._read_sheets(path, workbook)
        finally:
            # En modo read_only openpyxl mantiene el archivo abierto hasta
            # close(): en Windows eso bloquea borrar/mover el archivo (y
            # en cualquier SO fuga un descriptor por cada archivo leido).
            workbook.close()

    @staticmethod
    def _read_sheets(path: Path, workbook) -> list[IngestedDocument]:
        documents: list[IngestedDocument] = []

        for sheet in workbook.worksheets:
            rows = [
                row for row in sheet.iter_rows(values_only=True) if not _is_blank_row(row)
            ]
            if not rows:
                continue

            header_row, *data_rows = rows
            headers = [
                str(cell).strip() if cell is not None else f"columna_{index + 1}"
                for index, cell in enumerate(header_row)
            ]

            table_lines = [
                "| " + " | ".join(headers) + " |",
                "| " + " | ".join("---" for _ in headers) + " |",
            ]
            row_chunks: list[str] = []
            for row in data_rows:
                cells = ["" if cell is None else str(cell).strip() for cell in row]
                table_lines.append("| " + " | ".join(cells) + " |")
                pairs = [
                    f"{header}: {value}"
                    for header, value in zip(headers, cells)
                    if value
                ]
                if pairs:
                    row_chunks.append("; ".join(pairs))

            if not row_chunks:
                continue

            documents.append(
                IngestedDocument(
                    title=f"{path.stem} - {sheet.title}",
                    content="\n".join(table_lines),
                    chunks=row_chunks,
                )
            )

        return documents
