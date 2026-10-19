"""Tests del pipeline de ingestion (app/rag/ingestion) -- no necesitan
base de datos: los loaders son funciones puras sobre archivos, y eso es
justo lo que se prueba aca. El resto del flujo (RAGService.ingest_article
usando el `chunks` que devuelve un loader) esta en
tests/test_rag_service_ingest.py, con mocks."""

from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from app.rag.ingestion.base import DocumentLoadError, IngestedDocument
from app.rag.ingestion.docx_loader import DocxLoader
from app.rag.ingestion.excel_loader import ExcelLoader
from app.rag.ingestion.markdown_loader import MarkdownLoader
from app.rag.ingestion.pdf_loader import PDFLoader
from app.rag.ingestion.registry import (
    UnsupportedSourceError,
    load_documents,
    load_documents_from_dir,
)


def _make_minimal_pdf(text: str | None) -> bytes:
    """Arma un PDF valido minimo a mano (sin dependencias extra de
    generacion de PDF, que este proyecto no necesita para nada mas que
    este test) -- una pagina con un unico content stream que dibuja
    `text` con el operador Tj, o un content stream vacio si text es
    None (simula una pagina sin texto extraible, como un PDF
    escaneado)."""
    content_stream = (
        f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode() if text else b""
    )
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 4 0 R >>\nendobj\n",
        f"4 0 obj\n<< /Length {len(content_stream)} >>\nstream\n".encode()
        + content_stream
        + b"\nendstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(buf.tell())
        buf.write(obj)
    xref_offset = buf.tell()
    buf.write(f"xref\n0 {len(objects) + 1}\n".encode())
    buf.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buf.write(f"{offset:010d} 00000 n \n".encode())
    buf.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF".encode()
    )
    return buf.getvalue()


class TestMarkdownLoader:
    def test_usa_el_encabezado_h1_como_titulo(self, tmp_path):
        path = tmp_path / "articulo.md"
        path.write_text("# Como rastrear un envio\n\nTexto del articulo.\n")

        [document] = MarkdownLoader().load(path)

        assert document.title == "Como rastrear un envio"
        assert document.content == "Texto del articulo."
        assert document.chunks is None

    def test_sin_encabezado_usa_el_nombre_del_archivo(self, tmp_path):
        path = tmp_path / "notas-sueltas.md"
        path.write_text("Contenido sin encabezado H1.")

        [document] = MarkdownLoader().load(path)

        assert document.title == "notas-sueltas"


class TestPDFLoader:
    def test_extrae_texto_de_un_pdf_con_capa_de_texto(self, tmp_path):
        path = tmp_path / "manual.pdf"
        path.write_bytes(_make_minimal_pdf("Hola desde PDF de prueba"))

        [document] = PDFLoader().load(path)

        assert document.title == "manual"
        assert "Hola desde PDF de prueba" in document.content
        assert document.chunks is None

    def test_pdf_sin_texto_extraible_lanza_document_load_error(self, tmp_path):
        path = tmp_path / "escaneado.pdf"
        path.write_bytes(_make_minimal_pdf(None))

        with pytest.raises(DocumentLoadError):
            PDFLoader().load(path)

    def test_pdf_corrupto_lanza_document_load_error(self, tmp_path):
        path = tmp_path / "corrupto.pdf"
        path.write_bytes(b"esto no es un PDF valido")

        with pytest.raises(DocumentLoadError):
            PDFLoader().load(path)


class TestExcelLoader:
    def test_una_hoja_produce_un_documento_con_un_chunk_por_fila(self, tmp_path):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Tarifas"
        sheet.append(["Servicio", "Precio"])
        sheet.append(["Nacional", 25000])
        sheet.append(["Urbano", 12000])
        path = tmp_path / "tarifas.xlsx"
        workbook.save(path)

        [document] = ExcelLoader().load(path)

        assert document.title == "tarifas - Tarifas"
        assert document.chunks == [
            "Servicio: Nacional; Precio: 25000",
            "Servicio: Urbano; Precio: 12000",
        ]
        assert "| Servicio | Precio |" in document.content

    def test_una_hoja_vacia_no_produce_documento(self, tmp_path):
        workbook = Workbook()
        workbook.active.title = "ConDatos"
        workbook.active.append(["Servicio", "Precio"])
        workbook.active.append(["Nacional", 25000])
        workbook.create_sheet("Vacia")
        path = tmp_path / "mixto.xlsx"
        workbook.save(path)

        documents = ExcelLoader().load(path)

        assert [doc.title for doc in documents] == ["mixto - ConDatos"]


class TestRegistry:
    def test_load_documents_elige_el_loader_por_extension(self, tmp_path):
        path = tmp_path / "articulo.md"
        path.write_text("# Titulo\n\nContenido.")

        [document] = load_documents(path)

        assert isinstance(document, IngestedDocument)
        assert document.title == "Titulo"

    def test_load_documents_extension_no_soportada_lanza_error(self, tmp_path):
        path = tmp_path / "notas.txt"
        path.write_text("no deberia intentar leerse")

        with pytest.raises(UnsupportedSourceError):
            load_documents(path)

    def test_load_documents_from_dir_saltea_extensiones_no_soportadas(self, tmp_path):
        (tmp_path / "articulo.md").write_text("# Titulo\n\nContenido.")
        (tmp_path / "notas.txt").write_text("archivo suelto, no es fuente")

        documents = load_documents_from_dir(tmp_path)

        assert [doc.title for doc in documents] == ["Titulo"]

    def test_load_documents_from_dir_saltea_un_pdf_sin_texto_y_sigue_con_el_resto(
        self, tmp_path
    ):
        (tmp_path / "01-bueno.md").write_text("# Bueno\n\nContenido.")
        (tmp_path / "02-escaneado.pdf").write_bytes(_make_minimal_pdf(None))

        documents = load_documents_from_dir(tmp_path)

        assert [doc.title for doc in documents] == ["Bueno"]

    def test_load_documents_from_dir_orden_alfabetico(self, tmp_path):
        (tmp_path / "02-segundo.md").write_text("# Segundo\n\nB.")
        (tmp_path / "01-primero.md").write_text("# Primero\n\nA.")

        documents = load_documents_from_dir(tmp_path)

        assert [doc.title for doc in documents] == ["Primero", "Segundo"]


class TestDocxLoader:
    @staticmethod
    def _save(document, path):
        document.save(str(path))
        return path

    def test_titulos_y_parrafos_se_convierten_a_markdown(self, tmp_path):
        from docx import Document

        doc = Document()
        doc.add_heading("Procedimiento de reclamos", level=1)
        doc.add_paragraph("El analista revisa el caso en 48 horas.")
        doc.add_heading("Escalamiento", level=2)
        doc.add_paragraph("Si no hay respuesta, se escala al supervisor.")
        path = self._save(doc, tmp_path / "Procedimiento de reclamos.docx")

        [document] = DocxLoader().load(path)

        assert document.title == "Procedimiento de reclamos"
        assert "# Procedimiento de reclamos" in document.content
        assert "## Escalamiento" in document.content
        assert "El analista revisa el caso en 48 horas." in document.content

    def test_cada_fila_de_tabla_es_un_chunk_con_su_seccion(self, tmp_path):
        from docx import Document

        doc = Document()
        doc.add_heading("Articulos restringidos", level=1)
        table = doc.add_table(rows=3, cols=2)
        table.rows[0].cells[0].text = "Codigo"
        table.rows[0].cells[1].text = "Descripcion"
        table.rows[1].cells[0].text = "R-14"
        table.rows[1].cells[1].text = "Baterias de litio"
        table.rows[2].cells[0].text = "R-22"
        table.rows[2].cells[1].text = "Aerosoles"
        path = self._save(doc, tmp_path / "restringidos.docx")

        [document] = DocxLoader().load(path)

        assert document.chunks is not None
        assert "Articulos restringidos > Codigo: R-14; Descripcion: Baterias de litio" in document.chunks
        assert "Articulos restringidos > Codigo: R-22; Descripcion: Aerosoles" in document.chunks
        assert "| Codigo | Descripcion |" in document.content

    def test_las_listas_se_convierten_a_vinetas(self, tmp_path):
        from docx import Document

        doc = Document()
        doc.add_paragraph("Primer paso", style="List Bullet")
        doc.add_paragraph("Segundo paso", style="List Bullet")
        path = self._save(doc, tmp_path / "pasos.docx")

        [document] = DocxLoader().load(path)

        assert "- Primer paso" in document.content
        assert "- Segundo paso" in document.content

    def test_documento_sin_texto_lanza_error(self, tmp_path):
        from docx import Document

        path = self._save(Document(), tmp_path / "vacio.docx")

        with pytest.raises(DocumentLoadError):
            DocxLoader().load(path)

    def test_archivo_que_no_es_docx_lanza_error(self, tmp_path):
        path = tmp_path / "falso.docx"
        path.write_bytes(b"esto no es un zip")

        with pytest.raises(DocumentLoadError):
            DocxLoader().load(path)

    def test_el_registry_carga_docx_por_extension(self, tmp_path):
        from docx import Document

        doc = Document()
        doc.add_paragraph("Contenido de prueba.")
        path = self._save(doc, tmp_path / "nota.docx")

        [document] = load_documents(path)

        assert document.title == "nota"
