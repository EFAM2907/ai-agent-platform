"""Corre periodicamente la ingesta de kb_content/ para cada organizacion
configurada, en vez de depender de que alguien invoque
scripts/ingest_kb_articles.py a mano cada vez que el contenido cambia.

Lee los pares (organization_id, subcarpeta) desde el archivo apuntado
por settings.kb_ingestion_schedule_path (default:
kb_content/ingestion_schedule.yaml) -- lista plana de YAML, mismo
formato que eval_harness/datasets/*.yaml. Ese archivo es especifico de
cada deployment (los organization_id solo existen en esa base de datos
en particular), por eso no se versiona -- ver
kb_content/ingestion_schedule.yaml.example para la plantilla y copiarlo
sin el ".example".

  python -m scripts.run_kb_ingestion_scheduler          # bucle continuo
  python -m scripts.run_kb_ingestion_scheduler --once   # una sola pasada (cron)

Reusa ingest_kb_articles() de scripts.ingest_kb_articles sin duplicar
su logica de upsert -- correr esto repetidamente es seguro porque
RAGService.ingest_article hace upsert por (organization_id, titulo):
no duplica articulos, y actualiza los que cambiaron de contenido. Un
error en una entrada no frena a las demas, mismo patron de aislamiento
que scripts/run_gmail_poller.py.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from pathlib import Path

import yaml

from app.core.config import settings
from scripts.ingest_kb_articles import ingest_kb_articles

logger = logging.getLogger("kb_ingestion_scheduler")

_KB_CONTENT_ROOT = Path(__file__).parent.parent / "kb_content"


def _load_schedule() -> list[dict]:
    path = Path(settings.kb_ingestion_schedule_path)
    if not path.is_file():
        logger.warning(
            "No existe %s -- nada para ingerir. Copia "
            "kb_content/ingestion_schedule.yaml.example y agrega tus "
            "organization_id reales.",
            path,
        )
        return []
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or []


async def run_scheduled_ingestion() -> None:
    for entry in _load_schedule():
        organization_id = uuid.UUID(entry["organization_id"])
        articles_dir = _KB_CONTENT_ROOT / entry["dir"]
        if not articles_dir.is_dir():
            logger.warning(
                "org=%s: no existe kb_content/%s/", organization_id, entry["dir"]
            )
            continue
        try:
            count = await ingest_kb_articles(organization_id, articles_dir)
            logger.info(
                "org=%s dir=%s: %d documentos indexados",
                organization_id,
                entry["dir"],
                count,
            )
        except Exception:
            logger.exception(
                "org=%s dir=%s: fallo la ingesta", organization_id, entry["dir"]
            )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if "--once" in sys.argv:
        await run_scheduled_ingestion()
        return
    logger.info(
        "Scheduler de ingestion del KB iniciado (cada %ss)",
        settings.kb_ingestion_poll_interval_seconds,
    )
    while True:
        await run_scheduled_ingestion()
        await asyncio.sleep(settings.kb_ingestion_poll_interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
