"""Bradley-Terry-Luce ranking for bounded Robin tournaments."""

from __future__ import annotations

from co_scientist.orchestrator.btl import rank_btl


def test_btl_ranking_orders_candidates_by_pairwise_wins() -> None:
    scores = rank_btl(
        ["a", "b", "c"],
        [
            ("a", "b", "a"),
            ("a", "c", "a"),
            ("b", "c", "b"),
            ("a", "b", "a"),
        ],
    )

    assert list(scores) == ["a", "b", "c"]
    assert scores["a"] > scores["b"] > scores["c"]


def test_btl_returns_equal_scores_without_comparisons() -> None:
    scores = rank_btl(["a", "b"], [])

    assert scores == {"a": 0.0, "b": 0.0}
