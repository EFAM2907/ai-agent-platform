"""Reciprocal Rank Fusion (RRF): combina varios rankings de la misma
coleccion (aca: el de similitud vectorial y el de palabras clave) en uno
solo, usando unicamente la POSICION de cada item en cada ranking -- no
sus scores crudos.

Eso es lo que lo hace util para busqueda hibrida: una distancia coseno
(0..2, menor es mejor) y un ts_rank (sin cota fija, mayor es mejor) no
son comparables ni normalizables de forma honesta, pero "quedo 1ro" y
"quedo 3ro" si lo son. Un item que aparece alto en AMBOS rankings suma
dos aportes grandes y sube; uno que solo aparece en uno suma uno solo.

score(item) = sum( 1 / (k + posicion_en_ranking) ) sobre los rankings
donde aparece, con posicion empezando en 1. k=60 es el valor del paper
original (Cormack et al., 2009) y suaviza la diferencia entre las
primeras posiciones; no hace falta tunearlo para empezar.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence
from typing import TypeVar

T = TypeVar("T")

DEFAULT_RRF_K = 60


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[T]],
    *,
    key: Callable[[T], Hashable],
    k: int = DEFAULT_RRF_K,
) -> list[tuple[T, float]]:
    """Devuelve [(item, score_rrf)] ordenado de mayor a menor score.

    `key` identifica al mismo item entre rankings (ej. chunk.id). Si un
    item aparece en varios rankings, se conserva el objeto de la primera
    aparicion. Empates de score se resuelven por orden de primera
    aparicion (primero el ranking que vino antes en `rankings`), para
    que el resultado sea determinista."""
    scores: dict[Hashable, float] = {}
    items: dict[Hashable, T] = {}

    for ranking in rankings:
        for position, item in enumerate(ranking, start=1):
            item_key = key(item)
            scores[item_key] = scores.get(item_key, 0.0) + 1.0 / (k + position)
            items.setdefault(item_key, item)

    # sorted() es estable: los empates conservan el orden de insercion
    # del dict, que es el de primera aparicion.
    ordered_keys = sorted(scores, key=lambda item_key: scores[item_key], reverse=True)
    return [(items[item_key], scores[item_key]) for item_key in ordered_keys]
