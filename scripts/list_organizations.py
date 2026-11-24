"""Lista las organizaciones con conteos de datos. SOLO LECTURA.

Sirve para dos cosas al operar contra una base remota (p. ej. Cloud SQL, desde
un Cloud Run Job donde no hay shell):
  - obtener el id de una organizacion (lo piden los scripts de ingesta/seed);
  - verificar cuanto contenido tiene cada una tras cargar datos.

No imprime emails, contrasenas ni llaves.

Uso:
    python -m scripts.list_organizations
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.database import SessionLocal

_QUERY = text(
    """
    SELECT o.id, o.name, o.tax_id, (o.deleted_at IS NOT NULL) AS borrada,
           (SELECT count(*) FROM kb_articles a WHERE a.organization_id = o.id),
           (SELECT count(*) FROM kb_chunks c WHERE c.organization_id = o.id),
           (SELECT count(*) FROM customers cu WHERE cu.organization_id = o.id),
           (SELECT count(*) FROM shipments s WHERE s.organization_id = o.id)
    FROM organizations o
    ORDER BY o.name
    """
)


async def main() -> None:
    async with SessionLocal() as session:
        vector = (
            await session.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
        ).scalar()
        print(f"extension pgvector: {vector or 'NO INSTALADA'}")
        rows = (await session.execute(_QUERY)).all()

    print(f"organizaciones: {len(rows)}")
    for org_id, name, tax_id, deleted, articles, chunks, customers, shipments in rows:
        print(
            f"- id={org_id} | nombre={name!r} | tax_id={tax_id} | borrada={deleted} | "
            f"kb_articulos={articles} kb_chunks={chunks} clientes={customers} envios={shipments}"
        )


if __name__ == "__main__":
    asyncio.run(main())
