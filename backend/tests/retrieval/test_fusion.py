import uuid

import pytest

from repolens.ingest.chunk import Strategy
from repolens.retrieval.fusion import RRF_K, reciprocal_rank_fusion
from repolens.retrieval.types import RetrievedChunk


def chunk(name: str, score: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid5(uuid.NAMESPACE_URL, name),
        strategy=Strategy.NAIVE,
        path=name,
        start_line=1,
        end_line=2,
        language="python",
        symbol=None,
        kind="window",
        content=name,
        score=score,
    )


def fused(rankings: list[list[str]], *, top: int = 20, k: int = RRF_K) -> list[tuple[str, float]]:
    result = reciprocal_rank_fusion([[chunk(name) for name in r] for r in rankings], top=top, k=k)
    return [(item.path, item.score) for item in result]


def test_scores_sum_one_over_sixty_plus_rank() -> None:
    # a: rank 1 and 2. c: rank 3 and 1. b: rank 2 only. d: rank 3 only.
    assert fused([["a", "b", "c"], ["c", "a", "d"]]) == [
        ("a", pytest.approx(1 / 61 + 1 / 62)),
        ("c", pytest.approx(1 / 63 + 1 / 61)),
        ("b", pytest.approx(1 / 62)),
        ("d", pytest.approx(1 / 63)),
    ]


def test_hand_computed_values() -> None:
    (first, score), *_ = fused([["a", "b"], ["b", "a"], ["a"]])

    assert first == "a"
    assert score == pytest.approx(0.016393443 + 0.016129032 + 0.016393443)


def test_keeps_only_the_top_results() -> None:
    names = [f"c{index}" for index in range(30)]

    result = fused([names, list(reversed(names))], top=20)

    assert len(result) == 20


def test_ties_keep_first_appearance_order() -> None:
    assert [name for name, _ in fused([["x", "y"], ["y", "x"]])] == ["x", "y"]
    assert [name for name, _ in fused([["x"], ["y"]])] == ["x", "y"]


def test_the_fused_score_replaces_the_original() -> None:
    (only,) = reciprocal_rank_fusion([[chunk("a", score=0.99)]], top=5)

    assert only.score == pytest.approx(1 / 61)
    assert only.path == "a"


def test_the_constant_is_configurable() -> None:
    assert fused([["a"]], k=0) == [("a", pytest.approx(1.0))]


def test_no_rankings_fuse_to_nothing() -> None:
    assert fused([]) == []
    assert fused([[], []]) == []
