"""Ingesta los archivos de una subcarpeta de kb_content/ en la KB de una
organizacion. Soporta markdown (.md), PDF (.pdf), Excel (.xlsx) y Word
(.docx) en la misma carpeta, sin distinguir entre ellos a este nivel --
el formato real lo resuelve app.rag.ingestion.load_documents_from_dir,
eligiendo el loader por extension de archivo (ver
app/rag/ingestion/registry.py).

Uso:
    python -m scripts.ingest_kb_articles <organization_id>
    python -m scripts.ingest_kb_articles <organization_id> --dir inter_rapidisimo
    python -m scripts.ingest_kb_articles <organization_id> --dir inter_rapidisimo --force

Sin --dir, usa kb_content/articles/ (comportamiento original, para no
romper ningun uso existente). Con --dir <nombre>, ingiere
kb_content/<nombre>/* -- asi cada tenant tiene su propia subcarpeta y
un tenant nuevo nunca hereda articulos de otro por accidente.

Un archivo de extension no soportada, o un PDF sin texto extraible
(escaneado, sin OCR), se saltea con un warning en el log en vez de
abortar toda la ingesta -- ver
app.rag.ingestion.registry.load_documents_from_dir.

Idempotente y sincronizado con la carpeta (ver app.rag.sync):
- Correrlo dos veces no duplica articulos ni vuelve a llamar al
  proveedor de embeddings por lo que no cambio (hash del contenido).
- Un archivo cuyo contenido cambio se reindexa.
- Un archivo que ya NO esta en la carpeta retira su articulo de la KB
  (--no-prune lo desactiva). Si la carpeta esta vacia no se retira nada.
- --force reindexa todo aunque nada haya cambiado (necesario si cambia
  el algoritmo de chunking).
Seguro de correr periodicamente o despues de editar kb_content/.
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
from app.rag.models import SOURCE_FILE
from app.rag.repository import KBRepository
from app.rag.service import RAGService
from app.rag.sync import sync_documents

_KB_CONTENT_ROOT = Path(__file__).parent.parent / "kb_content"

# Procesos de fondo: esperan a que se renueve la cuota por minuto de
# embeddings en vez de fallar (ver app.rag.sync).
_RATE_LIMIT_ATTEMPTS = 5


async def ingest_kb_articles(
    organization_id: uuid.UUID,
    articles_dir: Path,
    *,
    force: bool = False,
    prune: bool = True,
) -> int:
    """Devuelve cuantos documentos quedaron indexados. Ver app.rag.sync."""
    async with SessionLocal() as session:
        org_repository = OrganizationRepository(session)
        organization = await org_repository.get_by_id(organization_id)
        if organization is None:
            raise ValueError(f"Organizacion {organization_id} no encontrada")

        virtual_key = resolve_tenant_virtual_key(organization)
        rag_service = RAGService(
            repository=KBRepository(session),
            embedding_client=EmbeddingClient(virtual_key=virtual_key),
            session=session,
        )

        documents = load_documents_from_dir(articles_dir)
        # source_ref con el nombre de la carpeta como prefijo: dos carpetas
        # de la misma organizacion no se retiran entre si al sincronizar.
        prefix = f"{articles_dir.name}/"
        for document in documents:
            document.source_ref = prefix + (document.source_ref or document.title)

        report = await sync_documents(
            rag_service,
            organization_id,
            documents,
            source_type=SOURCE_FILE,
            ref_prefix=prefix,
            prune=prune,
            force=force,
            rate_limit_attempts=_RATE_LIMIT_ATTEMPTS,
        )
        if report.pruned:
            print(f"  {report.pruned} documentos retirados (ya no estan en la carpeta)")
        return report.documents


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("organization_id", type=uuid.UUID)
    parser.add_argument(
        "--dir",
        default="articles",
        help="Subcarpeta de kb_content/ a ingerir (default: articles)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reindexar (chunking + embeddings) aunque el contenido no haya cambiado.",
    )
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="No retirar de la KB los documentos que ya no estan en la carpeta.",
    )
    args = parser.parse_args()

    articles_dir = _KB_CONTENT_ROOT / args.dir
    if not articles_dir.is_dir():
        raise SystemExit(f"No existe kb_content/{args.dir}/")

    count = await ingest_kb_articles(
        args.organization_id, articles_dir, force=args.force, prune=not args.no_prune
    )
    print(
        f"{count} documentos indexados para la organizacion {args.organization_id} "
        f"desde kb_content/{args.dir}/."
    )


if __name__ == "__main__":
    asyncio.run(main())
