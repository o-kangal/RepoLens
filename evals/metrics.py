"""Retrieval metrics over line ranges.

A retrieved chunk is a hit for an expected source when both are in the same file and their line
ranges (1-based, inclusive) overlap. Negative questions have no expected sources, so their
retrieval metrics are undefined: the functions return ``None`` and ``mean`` leaves them out.
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Span:
    path: str
    start: int
    end: int


def overlaps(a: Span, b: Span) -> bool:
    return a.path == b.path and a.start <= b.end and b.start <= a.end


def recall_at_k(retrieved: Sequence[Span], expected: Sequence[Span], k: int) -> float | None:
    """The share of expected sources that at least one of the top ``k`` chunks overlaps."""
    if not expected:
        return None
    top = retrieved[:k]
    found = sum(1 for source in expected if any(overlaps(chunk, source) for chunk in top))
    return found / len(expected)


def reciprocal_rank(retrieved: Sequence[Span], expected: Sequence[Span]) -> float | None:
    """``1 / rank`` of the first chunk that overlaps any expected source, 0 when none does."""
    if not expected:
        return None
    for rank, chunk in enumerate(retrieved, start=1):
        if any(overlaps(chunk, source) for source in expected):
            return 1 / rank
    return 0.0


def ndcg_at_k(retrieved: Sequence[Span], expected: Sequence[Span], k: int) -> float | None:
    """nDCG over the top ``k`` chunks, with a gain of 1 per expected source.

    Each expected source gains ``1 / log2(rank + 1)`` at the rank of the first chunk that
    overlaps it, so several chunks of one source (an AST split, say) count once. The ideal
    ranking finds one new source per position, up to ``k``. A chunk that covers several sources
    can beat that ideal, so the score is capped at 1.
    """
    if not expected:
        return None
    dcg = 0.0
    for source in expected:
        for rank, chunk in enumerate(retrieved[:k], start=1):
            if overlaps(chunk, source):
                dcg += 1 / math.log2(rank + 1)
                break
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(k, len(expected)) + 1))
    return min(1.0, dcg / ideal)


def mean(values: Iterable[float | None]) -> float | None:
    """The mean of the defined values, or ``None`` when there are none."""
    defined = [value for value in values if value is not None]
    return sum(defined) / len(defined) if defined else None
