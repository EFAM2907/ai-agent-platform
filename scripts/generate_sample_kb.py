"""Genera el corpus SINTETICO de prueba en kb_content/inter_rapidisimo/:
documentos md, pdf, xlsx y docx con contenido inventado (tarifas,
codigos de oficina y de novedad, plazos, circulares) para medir la
busqueda hibrida del RAG sobre algo mas realista que 8 articulos de
prosa. Nada de esto es informacion real de Inter Rapidisimo -- cada
documento lo dice en su pie (ver scripts/sample_kb_text.py).

Idempotente: reescribe siempre los mismos archivos con el mismo
contenido (los .md existentes 01-08 no se tocan).

Requiere las dependencias de generacion (fpdf2), que la app en runtime
no necesita:

    pip install -r requirements-dev.txt
    python -m scripts.generate_sample_kb

Despues, para llevarlo a la base:

    python -m scripts.ingest_kb_articles <organization_id> --dir inter_rapidisimo
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from fpdf import FPDF
from openpyxl import Workbook

from scripts.sample_kb_tables import WORKBOOKS
from scripts.sample_kb_text import DOCX_DOCS, MARKDOWN_DOCS, PDF_DOCS, SYNTHETIC_NOTICE

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "kb_content" / "inter_rapidisimo"


def write_markdown(output_dir: Path) -> int:
    for filename, content in MARKDOWN_DOCS.items():
        (output_dir / filename).write_text(content, encoding="utf-8", newline="\n")
    return len(MARKDOWN_DOCS)


def write_workbooks(output_dir: Path) -> int:
    for filename, sheets in WORKBOOKS.items():
        workbook = Workbook()
        workbook.remove(workbook.active)
        for sheet_name, (headers, rows) in sheets.items():
            sheet = workbook.create_sheet(sheet_name)
            sheet.append(headers)
            for row in rows:
                sheet.append(row)
        workbook.save(output_dir / f"{filename}.xlsx")
    return len(WORKBOOKS)


def write_pdfs(output_dir: Path) -> int:
    for filename, (title, sections) in PDF_DOCS.items():
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=18)
        pdf.add_page()

        pdf.set_font("Helvetica", "B", 15)
        pdf.multi_cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

        for heading, body in sections:
            pdf.set_font("Helvetica", "B", 11)
            pdf.multi_cell(0, 6, heading, new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 5, body, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

        pdf.ln(2)
        pdf.set_font("Helvetica", "I", 8)
        pdf.multi_cell(0, 4, SYNTHETIC_NOTICE, new_x="LMARGIN", new_y="NEXT")
        pdf.output(str(output_dir / f"{filename}.pdf"))
    return len(PDF_DOCS)


def write_docx(output_dir: Path) -> int:
    for filename, blocks in DOCX_DOCS.items():
        document = Document()
        for block in blocks:
            kind = block[0]
            if kind == "h1":
                document.add_heading(block[1], level=1)
            elif kind == "h2":
                document.add_heading(block[1], level=2)
            elif kind == "p":
                document.add_paragraph(block[1])
            elif kind == "li":
                document.add_paragraph(block[1], style="List Bullet")
            elif kind == "table":
                _, headers, rows = block
                table = document.add_table(rows=1, cols=len(headers))
                table.style = "Table Grid"
                for cell, header in zip(table.rows[0].cells, headers):
                    cell.text = header
                for row in rows:
                    cells = table.add_row().cells
                    for cell, value in zip(cells, row):
                        cell.text = str(value)
            else:
                raise ValueError(f"Bloque docx desconocido: {kind!r}")
        document.save(str(output_dir / f"{filename}.docx"))
    return len(DOCX_DOCS)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    counts = {
        "md": write_markdown(OUTPUT_DIR),
        "xlsx": write_workbooks(OUTPUT_DIR),
        "pdf": write_pdfs(OUTPUT_DIR),
        "docx": write_docx(OUTPUT_DIR),
    }
    print(f"Corpus sintetico escrito en {OUTPUT_DIR}")
    for kind, count in counts.items():
        print(f"  {kind}: {count} archivo(s)")


if __name__ == "__main__":
    main()
