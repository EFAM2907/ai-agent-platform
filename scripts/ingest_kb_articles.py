"""Ingesta los archivos de una subcarpeta de kb_content/ en la KB de una
organizacion. Soporta markdown (.md), PDF (.pdf) y Excel (.xlsx) en la
misma carpeta, sin distinguir entre ellos a este nivel -- el formato
real lo resuelve app.rag.ingestion.load_documents_from_dir, eligiendo
el loader por extension de archivo (ver app/rag/ingestion/registry.py).

Uso:
    python -m scripts.ingest_kb_articles <organization_id>
    python -m scripts.ingest_kb_articles <organization_id> --dir inter_rapidisimo

Sin --dir, usa kb_content/articles/ (comportamiento original, para no
romper ningun uso existente). Con --dir <nombre>, ingiere
kb_content/<nombre>/* -- asi cada tenant tiene su propia subcarpeta y
un tenant nuevo nunca hereda articulos de otro por accidente.

Un archivo de extension no soportada, o un PDF sin texto extraible
(escaneado, sin OCR), se saltea con un warning en el log en vez de
abortar toda la ingesta -- ver
app.rag.ingestion.registry.load_documents_from_dir.

Idempotente: RAGService.ingest_article hace upsert por (organization_id,
titulo) -- correr este script dos veces sobre la misma carpeta no
duplica articulos, y si un archivo fuente cambio de contenido, la
segunda corrida actualiza el articulo existente (contenido + chunks +
embeddings regenerados) en vez de sumar uno nuevo al lado. Seguro de
correr periodicamente o despues de editar kb_content/, no solo como
carga inicial.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from pathlib import Path

from app.core.database import SessionLocal
from app.organizations.dependencies import resolve_tenant_virtual_key
from app.organizations.repository import OrganizationRepository
from app.rag.embeddings import EmbeddingClient
from app.rag.ingestion import load_documents_from_dir
from app.rag.repository import KBRepository
from app.rag.service import RAGService

_KB_CONTENT_ROOT = Path(__file__).parent.parent / "kb_content"


async def ingest_kb_articles(organization_id: uuid.UUID, articles_dir: Path) -> int:
    async with SessionLocal() as session:
        org_repository = OrganizationRepository(session)
        organization = await org_repository.get_by_id(organization_id)
        if organization is None:
            raise ValueError(f"Organizacion {organization_id} no encontrada")

        virtual_key = resolve_tenant_virtual_key(organization)
        embedding_client = EmbeddingClient(virtual_key=virtual_key)
        rag_service = RAGService(
            repository=KBRepository(session),
            embedding_client=embedding_client,
            session=session,
        )

        documents = load_documents_from_dir(articles_dir)
        for document in documents:
            await rag_service.ingest_article(
                organization_id,
                document.title,
                document.content,
                chunks=document.chunks,
            )

        return len(documents)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("organization_id", type=uuid.UUID)
    parser.add_argument(
        "--dir",
        default="articles",
        help="Subcarpeta de kb_content/ a ingerir (default: articles)",
    )
    args = parser.parse_args()

    articles_dir = _KB_CONTENT_ROOT / args.dir
    if not articles_dir.is_dir():
        raise SystemExit(f"No existe kb_content/{args.dir}/")

    count = await ingest_kb_articles(args.organization_id, articles_dir)
    print(
        f"{count} documentos indexados para la organizacion {args.organization_id} "
        f"desde kb_content/{args.dir}/."
    )


if __name__ == "__main__":
    asyncio.run(main())
