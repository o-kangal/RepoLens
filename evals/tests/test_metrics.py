import math

import pytest

from metrics import Span, mean, ndcg_at_k, overlaps, recall_at_k, reciprocal_rank

AUTH = Span("src/auth.py", 10, 20)
CONFIG = Span("src/config.py", 1, 5)
EXPECTED = [AUTH, CONFIG]

# Hits at ranks 2 (AUTH), 4 (CONFIG, sharing only line 5) and 5 (AUTH again).
RETRIEVED = [
    Span("src/main.py", 1, 50),
    Span("src/auth.py", 15, 30),
    Span("src/users.py", 1, 40),
    Span("src/config.py", 5, 9),
    Span("src/auth.py", 1, 12),
]


@pytest.mark.parametrize(
    ("chunk", "hit"),
    [
        (Span("src/auth.py", 20, 30), True),
        (Span("src/auth.py", 1, 10), True),
        (Span("src/auth.py", 12, 14), True),
        (Span("src/auth.py", 1, 100), True),
        (Span("src/auth.py", 21, 30), False),
        (Span("src/auth.py", 1, 9), False),
        (Span("src/other.py", 10, 20), False),
    ],
    ids=["shares-the-end", "shares-the-start", "inside", "contains", "after", "before", "file"],
)
def test_a_hit_is_an_overlap_in_the_same_file(chunk: Span, hit: bool) -> None:
    assert overlaps(chunk, AUTH) is hit
    assert overlaps(AUTH, chunk) is hit


@pytest.mark.parametrize(("k", "recall"), [(1, 0.0), (2, 0.5), (4, 1.0), (5, 1.0), (10, 1.0)])
def test_recall_at_k_counts_expected_sources_found_in_the_top_k(k: int, recall: float) -> None:
    assert recall_at_k(RETRIEVED, EXPECTED, k) == recall


def test_recall_counts_a_source_once_however_many_chunks_overlap_it() -> None:
    assert recall_at_k(RETRIEVED, [AUTH], 5) == 1.0


def test_reciprocal_rank_is_one_over_the_first_hit() -> None:
    assert reciprocal_rank(RETRIEVED, EXPECTED) == 0.5
    assert reciprocal_rank(RETRIEVED[3:], EXPECTED) == 1.0


def test_ndcg_matches_the_hand_computed_value() -> None:
    # AUTH first found at rank 2, CONFIG at rank 4; rank 5 repeats AUTH and gains nothing.
    dcg = 1 / math.log2(3) + 1 / math.log2(5)
    ideal = 1 + 1 / math.log2(3)

    assert ndcg_at_k(RETRIEVED, EXPECTED, 10) == pytest.approx(dcg / ideal)
    assert ndcg_at_k(RETRIEVED, EXPECTED, 10) == pytest.approx(0.650920, abs=1e-6)


def test_ndcg_only_looks_at_the_top_k() -> None:
    # Only AUTH at rank 2 is inside the top 3.
    assert ndcg_at_k(RETRIEVED, EXPECTED, 3) == pytest.approx(
        (1 / math.log2(3)) / (1 + 1 / math.log2(3))
    )


def test_ideal_retrieval_scores_one() -> None:
    assert ndcg_at_k([AUTH, CONFIG], EXPECTED, 10) == pytest.approx(1.0)
    assert ndcg_at_k([AUTH], [AUTH], 10) == 1.0


def test_one_chunk_covering_several_sources_is_capped_at_one() -> None:
    whole_file = Span("src/auth.py", 1, 100)
    sources = [AUTH, Span("src/auth.py", 50, 60)]

    assert ndcg_at_k([whole_file], sources, 10) == 1.0
    assert recall_at_k([whole_file], sources, 5) == 1.0


def test_no_hit_scores_zero() -> None:
    misses = [Span("src/main.py", 1, 50), Span("src/auth.py", 21, 40)]

    assert recall_at_k(misses, EXPECTED, 10) == 0.0
    assert reciprocal_rank(misses, EXPECTED) == 0.0
    assert ndcg_at_k(misses, EXPECTED, 10) == 0.0
    assert recall_at_k([], EXPECTED, 10) == 0.0
    assert reciprocal_rank([], EXPECTED) == 0.0
    assert ndcg_at_k([], EXPECTED, 10) == 0.0


def test_negative_questions_have_no_retrieval_score() -> None:
    assert recall_at_k(RETRIEVED, [], 5) is None
    assert reciprocal_rank(RETRIEVED, []) is None
    assert ndcg_at_k(RETRIEVED, [], 10) is None


def test_the_mean_leaves_out_negative_questions() -> None:
    per_question = [
        reciprocal_rank(RETRIEVED, EXPECTED),
        reciprocal_rank(RETRIEVED, []),
        reciprocal_rank(RETRIEVED[3:], EXPECTED),
    ]

    assert mean(per_question) == 0.75
    assert mean([None, None]) is None
    assert mean([]) is None
