"""Ingesta los articulos markdown de una subcarpeta de kb_content/ en la
KB de una organizacion.

Uso:
    python -m scripts.ingest_kb_articles <organization_id>
    python -m scripts.ingest_kb_articles <organization_id> --dir inter_rapidisimo

Sin --dir, usa kb_content/articles/ (comportamiento original, para no
romper ningun uso existente). Con --dir <nombre>, ingiere
kb_content/<nombre>/*.md -- asi cada tenant tiene su propia subcarpeta y
un tenant nuevo nunca hereda articulos de otro por accidente.

Nota: no chequea si un articulo con el mismo titulo ya existe para esa
organizacion -- correrlo dos veces duplica los articulos. Para el
alcance actual (una carga inicial por tenant antes del review) eso es
aceptable; si este script pasa a correrse mas de una vez por tenant en
el futuro, agregarle idempotencia (como tiene
provision_existing_tenant_llm_keys.py para las virtual keys) antes de
reusarlo asi.
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
from app.rag.repository import KBRepository
from app.rag.service import RAGService

_KB_CONTENT_ROOT = Path(__file__).parent.parent / "kb_content"


def _parse_article(path: Path) -> tuple[str, str]:
    """Primera linea '# Titulo' -> titulo del articulo; el resto del
    archivo (sin esa linea) es el contenido que se chunkea e indexa."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        content = "\n".join(lines[1:]).strip()
    else:
        title = path.stem
        content = text.strip()
    return title, content


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

        article_paths = sorted(articles_dir.glob("*.md"))
        for path in article_paths:
            title, content = _parse_article(path)
            await rag_service.ingest_article(organization_id, title, content)

        return len(article_paths)


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
        f"{count} articulos indexados para la organizacion {args.organization_id} "
        f"desde kb_content/{args.dir}/."
    )


if __name__ == "__main__":
    asyncio.run(main())
