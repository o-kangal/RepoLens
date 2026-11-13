"""Reciprocal Rank Fusion: merge rankings by summing ``1 / (k + rank)`` per chunk."""

import uuid
from collections.abc import Sequence
from dataclasses import replace

from repolens.retrieval.types import RetrievedChunk

RRF_K = 60


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[RetrievedChunk]], *, top: int, k: int = RRF_K
) -> list[RetrievedChunk]:
    """Score each chunk ``Σ 1 / (k + rank)`` over the rankings it appears in (rank from 1).

    Returns the ``top`` chunks by that score, which replaces their original score. Ties keep
    the order in which the chunks first appear, earlier rankings first.
    """
    scores: dict[uuid.UUID, float] = {}
    chunks: dict[uuid.UUID, RetrievedChunk] = {}
    for ranking in rankings:
        for rank, chunk in enumerate(ranking, start=1):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (k + rank)
            chunks.setdefault(chunk.chunk_id, chunk)
    ordered = sorted(scores, key=lambda chunk_id: scores[chunk_id], reverse=True)
    return [replace(chunks[chunk_id], score=scores[chunk_id]) for chunk_id in ordered[:top]]
