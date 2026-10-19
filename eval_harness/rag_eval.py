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

Para decidir con numeros si la busqueda hibrida (vectorial + palabras
clave + RRF, ver RAGService.retrieve) mejora algo frente a solo
vectorial, sin gastar presupuesto de LLM (solo embeddings):

    python -m eval_harness.rag_eval <organization_id> --compare

Otras opciones: --mode vector|hybrid fuerza el modo del eval completo
(por defecto sigue settings.rag_hybrid_search_enabled).
"""

from __future__ import annotations

import argparse
import asyncio
import time
import uuid
from pathlib import Path

import yaml
from openai import RateLimitError

from app.core.config import settings
from app.core.database import SessionLocal
from app.llm.client import LLMClient
from app.llm.errors import InvalidResponseError
from app.llm.factory import get_default_llm_client
from app.llm.schemas import LLMRequest, Message, Role
from app.organizations.repository import OrganizationRepository
from app.rag.embeddings import EmbeddingClient
from app.rag.repository import KBRepository
from app.rag.service import RAGService
# Organization tiene relationships por nombre de clase (User, Customer...)
# -- sin importar sus modulos, el registro de mappers de SQLAlchemy no
# las ve y falla al resolverlas en el primer query. Mismo patron que
# scripts/seed_inter_rapidisimo_shipments.py.
import app.main  # noqa: E402,F401

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
) -> tuple[bool | None, str]:
    """Juez LLM barato: ¿la respuesta se sostiene en el contexto
    recuperado, sin afirmar nada que no este ahi? Devuelve (grounded,
    razon) -- la razon se imprime cuando falla, para no tener que
    adivinar por que.

    grounded es None si el juez no logro devolver un veredicto valido
    (el modelo barato a veces inventa sus propias llaves, ej.
    {"sostenida": true}, aun tras los reintentos de reparacion de
    LLMClient): eso es un fallo del JUEZ, no de la respuesta evaluada, y
    no debe contarse ni como acierto ni como error -- ni tumbar el resto
    de la corrida."""
    try:
        response = await _ask_judge(llm_client, query, context, answer)
    except InvalidResponseError as exc:
        return None, f"el juez no devolvio un veredicto valido ({str(exc).splitlines()[0][:120]})"
    parsed = response.parsed or {}
    return bool(parsed.get("grounded")), str(parsed.get("reason", ""))


async def _ask_judge(llm_client: LLMClient, query: str, context: str, answer: str):
    return await llm_client.generate(
        LLMRequest(
            messages=[
                Message(
                    role=Role.SYSTEM,
                    content=(
                        "Sos un juez que verifica si una respuesta esta "
                        "sostenida por el contexto dado, sin inventar nada "
                        "que no este ahi. Respondé SOLO con un objeto JSON que "
                        "tenga exactamente estas dos llaves: \"grounded\" "
                        "(true si la respuesta se sostiene enteramente en el "
                        "contexto, false si no) y \"reason\" (una frase "
                        "explicando por que). No uses otros nombres de llave."
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


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _answer_recall(case: dict, results: list) -> float | None:
    """Fraccion de los `expected_text` del caso que aparecen en el texto
    de los chunks recuperados. None si el caso no define ninguno.

    Distingue lo que el recall por titulo no puede: una tabla de 99 filas
    es UN articulo, asi que recuperar la fila equivocada del tarifario
    cuenta como acierto por titulo y como fallo aca."""
    expected_text = case.get("expected_text")
    if not expected_text:
        return None
    haystack = _normalize(" ".join(chunk.content for chunk, _, _ in results))
    found = sum(1 for text in expected_text if _normalize(text) in haystack)
    return found / len(expected_text)


def _retrieval_metrics(case: dict, results: list) -> tuple[float, float, list[str]]:
    """(precision, recall, titulos recuperados) de un caso -- compartido
    por el eval completo y por --compare para que ambos midan igual."""
    expected = set(case["expected_titles"])
    retrieved_titles = [title for _, title, _ in results]
    relevant_retrieved = sum(1 for title in retrieved_titles if title in expected)
    precision = relevant_retrieved / len(retrieved_titles) if retrieved_titles else 0.0
    recall = len(expected & set(retrieved_titles)) / len(expected) if expected else 1.0
    return precision, recall, retrieved_titles


class _CachedEmbeddingClient:
    """EmbeddingClient con cache por texto y espera ante limite de cuota.

    --compare corre CADA pregunta dos veces (vectorial e hibrido) y
    ambas embeben la misma query: sin cache se duplican las llamadas al
    proveedor, y el tier gratuito de Gemini limita las requests por
    minuto. Tampoco es un cambio de comportamiento del RAG -- solo del
    eval, que no debe fallar a mitad por una cuota."""

    _WAIT_SECONDS = 65
    _MAX_ATTEMPTS = 4

    def __init__(self, inner: EmbeddingClient) -> None:
        self._inner = inner
        self._cache: dict[str, list[float]] = {}

    async def embed_one(self, text: str) -> list[float]:
        if text in self._cache:
            return self._cache[text]
        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            try:
                vector = await self._inner.embed_one(text)
                break
            except RateLimitError:
                if attempt == self._MAX_ATTEMPTS:
                    raise
                print(f"  cuota de embeddings agotada, esperando {self._WAIT_SECONDS}s...")
                await asyncio.sleep(self._WAIT_SECONDS)
        self._cache[text] = vector
        return vector

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed_one(text) for text in texts]


def _build_rag_service(session) -> RAGService:
    if not settings.litellm_eval_virtual_key:
        raise RuntimeError(
            "LITELLM_EVAL_VIRTUAL_KEY no está configurada. Generala con "
            "'python -m scripts.create_eval_virtual_key' (proxy LiteLLM "
            "corriendo) y copiala a .env antes de correr este eval."
        )
    return RAGService(
        repository=KBRepository(session),
        embedding_client=_CachedEmbeddingClient(
            EmbeddingClient(virtual_key=settings.litellm_eval_virtual_key)
        ),
        session=session,
    )


async def run_comparison(organization_id: uuid.UUID, top_k_override: int | None = None) -> None:
    """Retrieval-only: corre cada pregunta del dataset en tres
    configuraciones -- solo vectorial, hibrida, e hibrida CON el filtro
    de relevancia (settings.rag_max_cosine_distance) -- y compara.
    Para las preguntas con respuesta mide precision y recall por titulo
    y recall por texto de la respuesta (`expected_text`), por grupo y en
    total. Para las `abstain` (fuera de la KB) mide cuantas devuelven
    contexto de todas formas; y cuantas preguntas legitimas bloquea el
    filtro por error. No llama a ningun LLM de generacion ni juez --
    solo al modelo de embeddings (la query se embebe una sola vez)."""
    with _DATASET_PATH.open("r", encoding="utf-8") as f:
        dataset = yaml.safe_load(f)

    # (nombre, hybrid, relevance_gate)
    modes = (("vector", False, False), ("hybrid", True, False), ("hybrid+gate", True, True))
    names = [name for name, _, _ in modes]
    stats: dict[str, dict[str, dict[str, list[float]]]] = {}
    returned_context = {name: 0 for name in names}  # abstain: devolvieron algo
    blocked = {name: 0 for name in names}  # legitimas: devolvieron []
    n_abstain = n_legit = 0

    async with SessionLocal() as session:
        organization = await OrganizationRepository(session).get_by_id(organization_id)
        if organization is None:
            raise RuntimeError(f"Organizacion {organization_id} no encontrada")
        rag_service = _build_rag_service(session)

        for case in dataset:
            group = case.get("group", "sin_grupo")
            top_k = top_k_override or case.get("top_k", 5)
            results_by_mode = {}
            for name, hybrid, gate in modes:
                results_by_mode[name] = await rag_service.retrieve(
                    organization_id, case["query"], top_k=top_k,
                    hybrid=hybrid, relevance_gate=gate,
                )

            if case.get("abstain"):
                n_abstain += 1
                counts = {name: len(results_by_mode[name]) for name in names}
                for name in names:
                    returned_context[name] += 1 if counts[name] else 0
                ok = "OK " if counts["hybrid+gate"] == 0 else "!! "
                print(
                    f"[{ok}] {case['id']} (fuera de la KB): fragmentos devueltos "
                    + " ".join(f"{name}={counts[name]}" for name in names)
                )
                continue

            n_legit += 1
            per_mode = {}
            for name in names:
                results = results_by_mode[name]
                precision, recall, _titles = _retrieval_metrics(case, results)
                answer = _answer_recall(case, results)
                per_mode[name] = (precision, recall, answer)
                if not results:
                    blocked[name] += 1
                bucket = stats.setdefault(group, {}).setdefault(
                    name, {"precision": [], "recall": [], "answer": []}
                )
                bucket["precision"].append(precision)
                bucket["recall"].append(recall)
                if answer is not None:
                    bucket["answer"].append(answer)

            v, h = per_mode["vector"], per_mode["hybrid"]
            v_score = v[2] if v[2] is not None else v[1]
            h_score = h[2] if h[2] is not None else h[1]
            marker = "=" if v_score == h_score else ("+" if h_score > v_score else "-")
            gate_note = "  <-- BLOQUEADA por el filtro" if not results_by_mode["hybrid+gate"] else ""
            answer_text = (
                f" | respuesta vector={v[2]:.2f} hibrido={h[2]:.2f}"
                if v[2] is not None and h[2] is not None
                else ""
            )
            print(
                f"[{marker}] {case['id']} ({group}): titulo vector={v[1]:.2f} "
                f"hibrido={h[1]:.2f}{answer_text}{gate_note}"
            )

    def _mean(values: list[float]) -> str:
        return f"{sum(values) / len(values):.1%}" if values else "   -  "

    top_k_text = f"top_k={top_k_override}" if top_k_override else "top_k del dataset (5)"
    print()
    print("=" * 76)
    print(f"Preguntas con respuesta en la KB ({n_legit}) -- {top_k_text}")
    print(f"{'grupo':<10}{'modo':<13}{'n':>4}{'precision':>12}{'recall titulo':>16}{'recall respuesta':>20}")
    overall: dict[str, dict[str, list[float]]] = {
        name: {"precision": [], "recall": [], "answer": []} for name in names
    }
    for group in sorted(stats):
        for name in names:
            bucket = stats[group][name]
            for key in overall[name]:
                overall[name][key].extend(bucket[key])
            print(
                f"{group:<10}{name:<13}{len(bucket['recall']):>4}"
                f"{_mean(bucket['precision']):>12}{_mean(bucket['recall']):>16}"
                f"{_mean(bucket['answer']):>20}"
            )
    print("-" * 76)
    for name in names:
        bucket = overall[name]
        print(
            f"{'TOTAL':<10}{name:<13}{len(bucket['recall']):>4}"
            f"{_mean(bucket['precision']):>12}{_mean(bucket['recall']):>16}"
            f"{_mean(bucket['answer']):>20}"
        )

    if n_abstain:
        print()
        print(f"Filtro de relevancia (umbral {settings.rag_max_cosine_distance})")
        print(f"{'modo':<13}{'fuera de la KB con contexto':>30}{'legitimas bloqueadas':>24}")
        for name in names:
            print(
                f"{name:<13}{returned_context[name]:>{27}}/{n_abstain:<2}"
                f"{blocked[name]:>20}/{n_legit}"
            )
    print()
    print("[+] hibrido mejor, [-] hibrido peor, [=] igual (por respuesta si el caso la define, si no por titulo).")


async def run_eval(organization_id: uuid.UUID, hybrid: bool | None = None) -> None:
    if not settings.litellm_eval_virtual_key:
        raise RuntimeError(
            "LITELLM_EVAL_VIRTUAL_KEY no está configurada. Generala con "
            "'python -m scripts.create_eval_virtual_key' (proxy LiteLLM "
            "corriendo) y copiala a .env antes de correr este eval."
        )

    with _DATASET_PATH.open("r", encoding="utf-8") as f:
        # Las `abstain` (fuera de la KB) no tienen fuente esperada: su recall
        # no significa nada. Se miden aparte con --compare.
        dataset = [case for case in yaml.safe_load(f) if not case.get("abstain")]

    async with SessionLocal() as session:
        organization = await OrganizationRepository(session).get_by_id(organization_id)
        if organization is None:
            raise RuntimeError(f"Organizacion {organization_id} no encontrada")

        llm_client = get_default_llm_client(
            tenant_virtual_key=settings.litellm_eval_virtual_key
        )
        rag_service = _build_rag_service(session)

        precisions: list[float] = []
        recalls: list[float] = []
        answer_recalls: list[float] = []
        grounded_count = 0
        unjudged = 0
        total_latency_ms = 0.0

        for case in dataset:
            started_at = time.perf_counter()
            results = await rag_service.retrieve(
                organization_id, case["query"], top_k=case.get("top_k", 5), hybrid=hybrid
            )
            latency_ms = (time.perf_counter() - started_at) * 1000
            total_latency_ms += latency_ms

            expected = set(case["expected_titles"])
            precision, recall, retrieved_titles = _retrieval_metrics(case, results)
            precisions.append(precision)
            recalls.append(recall)
            answer_recall = _answer_recall(case, results)
            if answer_recall is not None:
                answer_recalls.append(answer_recall)

            context = "\n\n".join(chunk.content for chunk, _, _ in results)
            answer = await _generate_answer(llm_client, case["query"], context)
            grounded, reason = await _judge_faithfulness(
                llm_client, case["query"], context, answer
            )
            if grounded is None:
                unjudged += 1
            elif grounded:
                grounded_count += 1

            verdict = "?" if grounded is None else ("si" if grounded else "NO")
            print(
                f"[{'OK' if recall == 1.0 else 'FALLO'}] {case['id']} ({case.get('group', '-')}): "
                f"precision={precision:.2f} recall={recall:.2f} "
                f"grounded={verdict} ({latency_ms:.0f}ms retrieval)",
                flush=True,
            )
            if recall < 1.0:
                missing = expected - set(retrieved_titles)
                print(f"    -> No se recuperó: {sorted(missing)}")
            if grounded is not True:
                print(f"    -> {reason}", flush=True)

        total = len(dataset)
        print()
        print("=" * 60)
        print(f"Precisión promedio de retrieval: {sum(precisions) / total:.1%}")
        print(f"Recall promedio de retrieval: {sum(recalls) / total:.1%}")
        if answer_recalls:
            print(
                "Recall por respuesta (casos con expected_text): "
                f"{sum(answer_recalls) / len(answer_recalls):.1%} "
                f"({len(answer_recalls)} casos)"
            )
        judged = total - unjudged
        print(
            "Faithfulness (respuestas sostenidas en el contexto): "
            f"{grounded_count}/{judged} ({grounded_count / judged:.1%})"
            if judged
            else "Faithfulness: ningun veredicto valido del juez"
        )
        if unjudged:
            print(f"  ({unjudged} casos sin veredicto valido del juez, excluidos del porcentaje)")
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
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Solo retrieval: compara vectorial vs hibrido, sin LLMs de generacion.",
    )
    parser.add_argument(
        "--mode",
        choices=("vector", "hybrid"),
        default=None,
        help="Fuerza el modo de busqueda del eval completo (default: settings).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="Con --compare: usa este top_k en todos los casos (default: el del dataset, 5).",
    )
    args = parser.parse_args()
    if args.compare:
        await run_comparison(args.organization_id, top_k_override=args.top_k)
        return
    hybrid = None if args.mode is None else args.mode == "hybrid"
    await run_eval(args.organization_id, hybrid=hybrid)


if __name__ == "__main__":
    asyncio.run(main())
