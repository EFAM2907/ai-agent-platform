"""
Eval harness para el RAG: mide, contra un dataset de preguntas con
fuente esperada, si RAGService.retrieve trae los articulos correctos
(precision/recall de retrieval) y si una respuesta generada a partir
de esos chunks se sostiene en ellos (faithfulness) -- en vez de asumir
que el RAG "funciona" porque las respuestas suenan razonables.

Mismo principio que eval_harness/model_routing_eval.py, aplicado a RAG
en vez de a ModelRouter -- y la misma razon de ser: RAGService.retrieve
no filtra por umbral de distancia coseno a proposito, porque calibrar
ese umbral necesita datos de eval que hasta ahora no existian (ver el
docstring de RAGService.retrieve). Este harness es ese primer dato.

Llama a LLMs reales (generar una respuesta a partir del contexto
recuperado, y un juez de faithfulness sobre esa respuesta) con la
virtual key separada de eval (LITELLM_EVAL_VIRTUAL_KEY) -- consume
presupuesto real, nunca el de un tenant real. Generala con
'python -m scripts.create_eval_virtual_key' si todavia no existe.

Requiere que la KB de la organizacion pasada ya este ingestada --
este harness no ingesta nada, solo mide retrieval y generacion contra
lo que ya exista. Para el dataset incluido (rag_golden.yaml, sobre
kb_content/inter_rapidisimo/):

    python -m scripts.ingest_kb_articles <organization_id> --dir inter_rapidisimo
    python -m eval_harness.rag_eval <organization_id>
"""

from __future__ import annotations

import argparse
import asyncio
import time
import uuid
from pathlib import Path

import yaml

from app.core.config import settings
from app.core.database import SessionLocal
from app.llm.client import LLMClient
from app.llm.factory import get_default_llm_client
from app.llm.schemas import LLMRequest, Message, Role
from app.organizations.repository import OrganizationRepository
from app.rag.embeddings import EmbeddingClient
from app.rag.repository import KBRepository
from app.rag.service import RAGService

_DATASET_PATH = Path(__file__).parent / "datasets" / "rag_golden.yaml"

# Modelo barato para ambos usos (generar la respuesta y juzgarla) --
# el corpus de eval es chico a proposito, no vale la pena el costo de
# un modelo caro todavia. Si la evidencia muestra que el juez barato no
# es confiable, es el primer parametro a cambiar.
_EVAL_MODEL = "gemini-3.5-flash-lite"

_FAITHFULNESS_SCHEMA = {
    "type": "object",
    "properties": {
        "grounded": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["grounded", "reason"],
}


async def _generate_answer(llm_client: LLMClient, query: str, context: str) -> str:
    response = await llm_client.generate(
        LLMRequest(
            messages=[
                Message(
                    role=Role.SYSTEM,
                    content=(
                        "Respondé la pregunta del usuario usando UNICAMENTE la "
                        "informacion del contexto. Si el contexto no alcanza "
                        "para responder, decilo explicitamente en vez de "
                        "inventar."
                    ),
                ),
                Message(
                    role=Role.USER,
                    content=f"Contexto:\n{context}\n\nPregunta: {query}",
                ),
            ],
            model=_EVAL_MODEL,
            request_tag="rag_eval_answer",
        )
    )
    return response.content


async def _judge_faithfulness(
    llm_client: LLMClient, query: str, context: str, answer: str
) -> tuple[bool, str]:
    """Juez LLM barato: ¿la respuesta se sostiene en el contexto
    recuperado, sin afirmar nada que no este ahi? Devuelve (grounded,
    razon) -- la razon se imprime cuando falla, para no tener que
    adivinar por que."""
    response = await llm_client.generate(
        LLMRequest(
            messages=[
                Message(
                    role=Role.SYSTEM,
                    content=(
                        "Sos un juez que verifica si una respuesta esta "
                        "sostenida por el contexto dado, sin inventar nada "
                        "que no este ahi. Respondé solo con el JSON pedido."
                    ),
                ),
                Message(
                    role=Role.USER,
                    content=(
                        f"Contexto:\n{context}\n\nPregunta: {query}\n\n"
                        f"Respuesta a evaluar: {answer}\n\n"
                        "¿La respuesta se sostiene enteramente en el "
                        "contexto (sin afirmar nada que no este ahi)?"
                    ),
                ),
            ],
            model=_EVAL_MODEL,
            response_schema=_FAITHFULNESS_SCHEMA,
            request_tag="rag_eval_judge",
        )
    )
    parsed = response.parsed or {}
    return bool(parsed.get("grounded")), str(parsed.get("reason", ""))


async def run_eval(organization_id: uuid.UUID) -> None:
    if not settings.litellm_eval_virtual_key:
        raise RuntimeError(
            "LITELLM_EVAL_VIRTUAL_KEY no está configurada. Generala con "
            "'python -m scripts.create_eval_virtual_key' (proxy LiteLLM "
            "corriendo) y copiala a .env antes de correr este eval."
        )

    with _DATASET_PATH.open("r", encoding="utf-8") as f:
        dataset = yaml.safe_load(f)

    async with SessionLocal() as session:
        organization = await OrganizationRepository(session).get_by_id(organization_id)
        if organization is None:
            raise RuntimeError(f"Organizacion {organization_id} no encontrada")

        llm_client = get_default_llm_client(
            tenant_virtual_key=settings.litellm_eval_virtual_key
        )
        embedding_client = EmbeddingClient(virtual_key=settings.litellm_eval_virtual_key)
        rag_service = RAGService(
            repository=KBRepository(session),
            embedding_client=embedding_client,
            session=session,
        )

        precisions: list[float] = []
        recalls: list[float] = []
        grounded_count = 0
        total_latency_ms = 0.0

        for case in dataset:
            started_at = time.perf_counter()
            results = await rag_service.retrieve(
                organization_id, case["query"], top_k=case.get("top_k", 5)
            )
            latency_ms = (time.perf_counter() - started_at) * 1000
            total_latency_ms += latency_ms

            expected = set(case["expected_titles"])
            retrieved_titles = [title for _, title, _ in results]
            relevant_retrieved = sum(1 for title in retrieved_titles if title in expected)
            precision = relevant_retrieved / len(retrieved_titles) if retrieved_titles else 0.0
            recall = (
                len(expected & set(retrieved_titles)) / len(expected) if expected else 1.0
            )
            precisions.append(precision)
            recalls.append(recall)

            context = "\n\n".join(chunk.content for chunk, _, _ in results)
            answer = await _generate_answer(llm_client, case["query"], context)
            grounded, reason = await _judge_faithfulness(
                llm_client, case["query"], context, answer
            )
            if grounded:
                grounded_count += 1

            print(
                f"[{'OK' if recall == 1.0 else 'FALLO'}] {case['id']}: "
                f"precision={precision:.2f} recall={recall:.2f} "
                f"grounded={'si' if grounded else 'NO'} ({latency_ms:.0f}ms retrieval)"
            )
            if recall < 1.0:
                missing = expected - set(retrieved_titles)
                print(f"    -> No se recuperó: {sorted(missing)}")
            if not grounded:
                print(f"    -> {reason}")

        total = len(dataset)
        print()
        print("=" * 60)
        print(f"Precisión promedio de retrieval: {sum(precisions) / total:.1%}")
        print(f"Recall promedio de retrieval: {sum(recalls) / total:.1%}")
        print(
            "Faithfulness (respuestas sostenidas en el contexto): "
            f"{grounded_count}/{total} ({grounded_count / total:.1%})"
        )
        print(f"Latencia promedio de retrieval: {total_latency_ms / total:.0f}ms")
        print()
        if sum(recalls) / total < 1.0:
            print(
                "Hay preguntas que no recuperan todas sus fuentes esperadas -- "
                "candidatas concretas para evaluar chunking adaptativo (ver "
                "docs/roadmap.md punto 4) antes de asumir que ya hace falta."
            )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("organization_id", type=uuid.UUID)
    args = parser.parse_args()
    await run_eval(args.organization_id)


if __name__ == "__main__":
    asyncio.run(main())
