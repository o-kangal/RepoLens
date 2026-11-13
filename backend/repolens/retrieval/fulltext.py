"""Full-text search: the top k chunks of one repository and strategy by ``ts_rank``.

The query is normalised like the indexed content (``chunks.tsv``): every run of characters that
are not letters or digits is a word break. Each whitespace-separated term of the query becomes
a phrase, so the identifier ``derive_key`` matches ``derive`` followed by ``key``, and the terms
are combined with OR, so a question matches chunks that share any of its words. English stop
words ("how", "does", "the") are dropped first, since the ``simple`` configuration keeps them
and they would match almost every chunk.

This search does not check the repository's index status; ``repolens.retrieval.search`` runs
it beside the vector search, which does.
"""

import asyncio
import uuid

from sqlalchemy import ColumnElement, func, literal, select, text
from sqlalchemy.orm import Session, sessionmaker

from repolens.db.models import Chunk
from repolens.ingest.chunk import Strategy
from repolens.retrieval.types import RetrievedChunk

_WORD_BREAK = "[^[:alnum:]]+"


async def fulltext_search(
    session_factory: sessionmaker[Session],
    *,
    repo_id: uuid.UUID,
    strategy: Strategy,
    query: str,
    k: int,
) -> list[RetrievedChunk]:
    return await asyncio.to_thread(_ranked, session_factory, repo_id, strategy, query, k)


def _ranked(
    session_factory: sessionmaker[Session],
    repo_id: uuid.UUID,
    strategy: Strategy,
    query: str,
    k: int,
) -> list[RetrievedChunk]:
    with session_factory() as session:
        terms = _content_terms(session, query.split())
        if not terms:
            return []
        tsquery = _phrase(terms[0])
        for term in terms[1:]:
            tsquery = tsquery.op("||")(_phrase(term))
        rank = func.ts_rank(Chunk.tsv, tsquery)
        rows = session.execute(
            select(Chunk, rank)
            .where(
                Chunk.repo_id == repo_id,
                Chunk.strategy == strategy,
                Chunk.tsv.op("@@")(tsquery),
            )
            .order_by(rank.desc(), Chunk.id)
            .limit(k)
        )
        return [_retrieved(chunk, float(score)) for chunk, score in rows]


def _phrase(term: str) -> ColumnElement[str]:
    """``term`` as a phrase of the words it splits into, like the indexed content."""
    words = func.regexp_replace(literal(term), _WORD_BREAK, " ", "g")
    return func.phraseto_tsquery("simple", words)


def _content_terms(session: Session, terms: list[str]) -> list[str]:
    """The terms that are not English stop words or bare punctuation, in their order."""
    if not terms:
        return []
    rows = session.scalars(
        text(
            "SELECT term FROM unnest(CAST(:terms AS text[])) WITH ORDINALITY AS t(term, n) "
            "WHERE to_tsvector('english', regexp_replace(term, :word_break, ' ', 'g')) "
            "<> ''::tsvector ORDER BY n"
        ),
        {"terms": terms, "word_break": _WORD_BREAK},
    )
    return list(rows)


def _retrieved(chunk: Chunk, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk.id,
        strategy=Strategy(chunk.strategy),
        path=chunk.path,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        language=chunk.language,
        symbol=chunk.symbol,
        kind=chunk.kind,
        content=chunk.content,
        score=score,
    )
