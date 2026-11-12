import uuid

from repolens.generation.context import Source, build_context
from repolens.ingest.chunk import ChunkKind, Strategy
from repolens.ingest.tokens import count_tokens
from repolens.retrieval.types import RetrievedChunk


def numbered(start: int, end: int, path: str = "a.py") -> str:
    return "".join(f"line_{index} = '{path}'\n" for index in range(start, end + 1))


def chunk(
    path: str,
    start: int,
    end: int,
    *,
    kind: ChunkKind = ChunkKind.WINDOW,
    content: str | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        strategy=Strategy.NAIVE,
        path=path,
        start_line=start,
        end_line=end,
        language="python",
        symbol=None,
        kind=kind,
        content=content if content is not None else numbered(start, end, path),
        score=1.0,
    )


def spans(sources: list[Source]) -> list[tuple[str, str, int, int]]:
    return [(source.label, source.path, source.start_line, source.end_line) for source in sources]


def test_sources_are_ordered_by_file_and_labelled_in_that_order() -> None:
    ranked = [chunk("b.py", 10, 12), chunk("a.py", 40, 41), chunk("a.py", 1, 3)]

    assert spans(build_context(ranked)) == [
        ("S1", "a.py", 1, 3),
        ("S2", "a.py", 40, 41),
        ("S3", "b.py", 10, 12),
    ]


def test_budget_keeps_the_best_ranked_chunks_that_fit() -> None:
    first, second, third = chunk("a.py", 1, 10), chunk("b.py", 1, 10), chunk("c.py", 1, 10)
    one_chunk = count_tokens(first.content)

    sources = build_context([first, second, third], budget_tokens=one_chunk * 2)

    assert [source.path for source in sources] == ["a.py", "b.py"]
    assert sum(count_tokens(source.content) for source in sources) <= one_chunk * 2


def test_a_chunk_too_large_is_skipped_and_smaller_ones_still_fill_the_budget() -> None:
    large, small = chunk("big.py", 1, 200), chunk("small.py", 1, 2)

    sources = build_context([large, small], budget_tokens=count_tokens(small.content) + 5)

    assert [source.path for source in sources] == ["small.py"]


def test_nothing_fits_an_empty_budget() -> None:
    assert build_context([chunk("a.py", 1, 3)], budget_tokens=0) == []


def test_chunk_inside_a_picked_range_is_dropped() -> None:
    sources = build_context([chunk("a.py", 1, 20), chunk("a.py", 5, 8)])

    assert spans(sources) == [("S1", "a.py", 1, 20)]


def test_overlapping_window_is_trimmed_to_its_new_lines() -> None:
    sources = build_context([chunk("a.py", 10, 20), chunk("a.py", 17, 30), chunk("a.py", 1, 12)])

    assert spans(sources) == [("S1", "a.py", 1, 9), ("S2", "a.py", 10, 20), ("S3", "a.py", 21, 30)]
    assert sources[0].content == numbered(1, 9)
    assert sources[2].content == numbered(21, 30)


def test_trimmed_lines_count_against_the_budget_not_the_whole_window() -> None:
    picked = chunk("a.py", 1, 20)
    overlapping = chunk("a.py", 18, 22)
    budget = count_tokens(picked.content) + count_tokens(numbered(21, 22))

    sources = build_context([picked, overlapping], budget_tokens=budget)

    assert spans(sources) == [("S1", "a.py", 1, 20), ("S2", "a.py", 21, 22)]


def test_window_split_in_two_by_a_picked_range_is_dropped() -> None:
    sources = build_context([chunk("a.py", 5, 6), chunk("a.py", 1, 10)])

    assert spans(sources) == [("S1", "a.py", 5, 6)]


def test_overlapping_chunk_with_a_header_is_dropped_not_trimmed() -> None:
    section = chunk(
        "README.md", 3, 9, kind=ChunkKind.SECTION, content="Install\n\n## Install\nrun it\n"
    )

    sources = build_context([chunk("README.md", 1, 5), section])

    assert spans(sources) == [("S1", "README.md", 1, 5)]


def test_same_lines_in_different_files_do_not_overlap() -> None:
    sources = build_context([chunk("a.py", 1, 5), chunk("b.py", 1, 5)])

    assert spans(sources) == [("S1", "a.py", 1, 5), ("S2", "b.py", 1, 5)]


def test_trimming_that_leaves_only_blank_lines_drops_the_chunk() -> None:
    window = chunk("a.py", 1, 4, content="x = 1\ny = 2\n\n\n")

    sources = build_context([chunk("a.py", 1, 2, content="x = 1\ny = 2\n"), window])

    assert spans(sources) == [("S1", "a.py", 1, 2)]
