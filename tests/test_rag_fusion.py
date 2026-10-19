"""reciprocal_rank_fusion es una funcion pura: se prueba sin base de datos."""

from __future__ import annotations

import pytest

from app.rag.fusion import reciprocal_rank_fusion


def _fuse(*rankings, k=60):
    return reciprocal_rank_fusion(rankings, key=lambda item: item, k=k)


def test_item_in_both_rankings_beats_item_that_is_first_in_only_one():
    # "b" es 2do en ambos; "a" es 1ro solo en el primero, "c" 1ro solo en el segundo.
    fused = _fuse(["a", "b"], ["c", "b"])

    assert [item for item, _ in fused][0] == "b"


def test_score_is_sum_of_reciprocal_ranks():
    fused = dict(_fuse(["a", "b"], ["b", "a"], k=60))

    expected = 1 / 61 + 1 / 62
    assert fused["a"] == pytest.approx(expected)
    assert fused["b"] == pytest.approx(expected)


def test_ties_keep_order_of_first_appearance():
    # a y c empatan (ambos 1ros en un solo ranking): gana el que aparecio antes.
    fused = _fuse(["a"], ["c"])

    assert [item for item, _ in fused] == ["a", "c"]


def test_items_present_in_only_one_ranking_are_kept():
    fused = _fuse(["a", "b"], [])

    assert [item for item, _ in fused] == ["a", "b"]


def test_empty_input_returns_empty_list():
    assert _fuse() == []
    assert _fuse([], []) == []


def test_key_function_merges_distinct_objects_that_share_an_identity():
    first = {"id": 1, "from": "vector"}
    same_item_from_keyword = {"id": 1, "from": "keyword"}
    other = {"id": 2, "from": "keyword"}

    fused = reciprocal_rank_fusion(
        [[first], [same_item_from_keyword, other]], key=lambda item: item["id"]
    )

    assert [item["id"] for item, _ in fused] == [1, 2]
    assert fused[0][0]["from"] == "vector"  # se conserva el objeto de la primera aparicion


def test_larger_k_flattens_the_gap_between_positions():
    steep = dict(_fuse(["a", "b"], k=1))
    flat = dict(_fuse(["a", "b"], k=1000))

    assert steep["a"] / steep["b"] > flat["a"] / flat["b"]
