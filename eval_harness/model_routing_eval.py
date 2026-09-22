"""
Eval harness para el ModelRouter: mide contra un dataset etiquetado a
mano si el clasificador barato de verdad acierta -- en vez de asumir
que "suena razonable" porque el prompt está bien redactado.

Llama a LLMs reales (Gemini para clasificar, y potencialmente
claude-sonnet-5 para los casos que el router mande al tier caro) --
consume presupuesto real. Usa su propia virtual key
(LITELLM_EVAL_VIRTUAL_KEY), separada de la de emergencia y de las de
tenants reales, generada con:
    python -m scripts.create_eval_virtual_key

Uso:
    python -m eval_harness.model_routing_eval
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import yaml

from app.core.config import settings
from app.llm.client import LLMClient
from app.llm.factory import get_default_llm_client
from app.llm.routing import ModelRouter

_DATASET_PATH = Path(__file__).parent / "datasets" / "model_routing_golden.yaml"


async def run_eval() -> None:
    if not settings.litellm_eval_virtual_key:
        raise RuntimeError(
            "LITELLM_EVAL_VIRTUAL_KEY no está configurada. Generala con "
            "'python -m scripts.create_eval_virtual_key' (proxy LiteLLM "
            "corriendo) y copiala a .env antes de correr este eval."
        )

    with _DATASET_PATH.open("r", encoding="utf-8") as f:
        dataset = yaml.safe_load(f)

    llm_client: LLMClient = get_default_llm_client(
        tenant_virtual_key=settings.litellm_eval_virtual_key
    )
    router = ModelRouter(
        llm_client,
        cheap_model="gemini-3.6-flash",
        expensive_model="claude-sonnet-5",
    )

    correct = 0
    false_positives = []
    false_negatives = []
    total_latency_ms = 0.0

    for case in dataset:
        started_at = time.perf_counter()
        chosen_model = await router.choose_model(case["query"])
        latency_ms = (time.perf_counter() - started_at) * 1000
        total_latency_ms += latency_ms

        predicted_expensive = chosen_model == "claude-sonnet-5"
        expected_expensive = case["expected_needs_expensive"]

        if predicted_expensive == expected_expensive:
            correct += 1
        elif predicted_expensive and not expected_expensive:
            false_positives.append(case["id"])
        elif not predicted_expensive and expected_expensive:
            false_negatives.append(case["id"])

        print(
            f"[{'OK' if predicted_expensive == expected_expensive else 'FALLO'}] "
            f"{case['id']}: esperado={'caro' if expected_expensive else 'barato'}, "
            f"predicho={'caro' if predicted_expensive else 'barato'} "
            f"({latency_ms:.0f}ms)"
        )

    total = len(dataset)
    accuracy = correct / total

    print()
    print("=" * 60)
    print(f"Accuracy del clasificador: {correct}/{total} ({accuracy:.1%})")
    print(f"Latencia promedio de clasificación: {total_latency_ms / total:.0f}ms")
    print()
    if false_positives:
        print(f"Falsos positivos (routeó a caro sin necesidad): {false_positives}")
        print("  -> Costo desperdiciado. Revisa el prompt del clasificador.")
    if false_negatives:
        print(f"Falsos negativos (routeó a barato debiendo ir a caro): {false_negatives}")
        print(
            "  -> MÁS GRAVE: una incidencia compleja/riesgosa fue manejada"
            " por el modelo simple. Revisa estos casos con prioridad."
        )
    if not false_positives and not false_negatives:
        print("Sin errores en este dataset -- considera ampliarlo con más"
              " casos límite antes de confiar del todo en el clasificador.")


if __name__ == "__main__":
    asyncio.run(run_eval())
