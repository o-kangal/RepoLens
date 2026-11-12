"""Naive baseline: fixed token windows with overlap, split on line boundaries."""

from repolens.ingest.chunk import Chunk, ChunkKind, Strategy, make_chunk
from repolens.ingest.tokens import count_tokens, split_lines
from repolens.ingest.walker import SourceFile

WINDOW_TOKENS = 400
OVERLAP_TOKENS = 60  # 15% of the window


def chunk_naive(
    source: SourceFile,
    *,
    window_tokens: int = WINDOW_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[Chunk]:
    """Cut ``source`` into windows of whole lines.

    A window grows until the next line would push it past ``window_tokens``; a single line that
    is larger on its own becomes its own window. Each next window starts on the latest lines of
    the previous one that fit in ``overlap_tokens``, and always moves forward.
    """
    lines = split_lines(source.text)
    counts = [count_tokens(line) for line in lines]
    chunks: list[Chunk] = []
    start = 0

    while start < len(lines):
        end, total = start, 0
        while end < len(lines) and (end == start or total + counts[end] <= window_tokens):
            total += counts[end]
            end += 1

        content = "".join(lines[start:end])
        if content.strip():
            chunks.append(
                make_chunk(
                    strategy=Strategy.NAIVE,
                    source=source,
                    kind=ChunkKind.WINDOW,
                    start_line=start + 1,
                    end_line=end,
                    content=content,
                )
            )
        if end == len(lines):
            break

        next_start, overlap = end, 0
        while next_start - 1 > start and overlap + counts[next_start - 1] <= overlap_tokens:
            next_start -= 1
            overlap += counts[next_start]
        start = next_start

    return chunks
