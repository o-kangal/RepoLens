"""Context builder: choose the sources the LLM sees, within a token budget."""

from collections.abc import Sequence
from dataclasses import dataclass

from repolens.ingest.chunk import ChunkKind
from repolens.ingest.tokens import count_tokens, split_lines
from repolens.retrieval.types import RetrievedChunk

DEFAULT_BUDGET_TOKENS = 6000


@dataclass(frozen=True, slots=True)
class Source:
    label: str
    """The citation marker the answer uses, such as ``S1``."""
    path: str
    start_line: int
    end_line: int
    content: str


@dataclass(frozen=True, slots=True)
class _Span:
    path: str
    start_line: int
    end_line: int
    content: str


def build_context(
    chunks: Sequence[RetrievedChunk], *, budget_tokens: int = DEFAULT_BUDGET_TOKENS
) -> list[Source]:
    """Pick chunks in ranked order until the budget is spent, then label them by file order.

    A chunk inside a range already picked is dropped. A window that overlaps one is trimmed to
    its new lines, since window content is the file's lines verbatim; other kinds carry headers,
    so an overlapping one is dropped. A chunk that does not fit is skipped and smaller ones are
    still tried. The budget counts source contents only.
    """
    picked: list[_Span] = []
    spent = 0
    for chunk in chunks:
        span = _without_overlap(chunk, picked)
        if span is None:
            continue
        tokens = count_tokens(span.content)
        if spent + tokens > budget_tokens:
            continue
        picked.append(span)
        spent += tokens

    ordered = sorted(picked, key=lambda span: (span.path, span.start_line))
    return [
        Source(
            label=f"S{index}",
            path=span.path,
            start_line=span.start_line,
            end_line=span.end_line,
            content=span.content,
        )
        for index, span in enumerate(ordered, start=1)
    ]


def _without_overlap(chunk: RetrievedChunk, picked: Sequence[_Span]) -> _Span | None:
    covered = {
        line
        for span in picked
        if span.path == chunk.path
        for line in range(span.start_line, span.end_line + 1)
    }
    lines = range(chunk.start_line, chunk.end_line + 1)
    if not covered.intersection(lines):
        return _Span(chunk.path, chunk.start_line, chunk.end_line, chunk.content)
    if chunk.kind != ChunkKind.WINDOW:
        return None

    kept = [line for line in lines if line not in covered]
    if not kept or kept[-1] - kept[0] + 1 != len(kept):
        return None  # fully covered, or the new lines are not one contiguous run
    content_lines = split_lines(chunk.content)
    first = kept[0] - chunk.start_line
    content = "".join(content_lines[first : first + len(kept)])
    if not content.strip():
        return None
    return _Span(chunk.path, kept[0], kept[-1], content)
