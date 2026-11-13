"""The chunk type every chunking strategy produces, mirroring the ``chunks`` table."""

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from repolens.ingest.tokens import count_tokens
from repolens.ingest.walker import Language, SourceFile


class Strategy(StrEnum):
    NAIVE = "naive"
    AST = "ast"
    MARKDOWN = "markdown"


class ChunkKind(StrEnum):
    WINDOW = "window"
    SECTION = "section"
    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"


@dataclass(frozen=True, slots=True)
class Chunk:
    strategy: Strategy
    path: str
    language: Language
    symbol: str | None
    kind: ChunkKind
    start_line: int
    """First line of the chunk in the file, 1-indexed."""
    end_line: int
    """Last line of the chunk in the file, inclusive."""
    content: str
    token_count: int
    content_hash: str
    """SHA-256 of ``content``, the text that gets embedded."""


def make_chunk(
    *,
    strategy: Strategy,
    source: SourceFile,
    kind: ChunkKind,
    start_line: int,
    end_line: int,
    content: str,
    symbol: str | None = None,
) -> Chunk:
    return Chunk(
        strategy=strategy,
        path=source.path,
        language=source.language,
        symbol=symbol,
        kind=kind,
        start_line=start_line,
        end_line=end_line,
        content=content,
        token_count=count_tokens(content),
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
