import hashlib
from itertools import pairwise

import pytest

from repolens.ingest.chunk import ChunkKind, Strategy
from repolens.ingest.naive import OVERLAP_TOKENS, WINDOW_TOKENS, chunk_naive
from repolens.ingest.tokens import count_tokens, split_lines
from repolens.ingest.walker import Language, SourceFile


def source(text: str, path: str = "src/app.py") -> SourceFile:
    return SourceFile(path=path, language=Language.PYTHON, text=text)


def numbered_lines(count: int) -> str:
    return "".join(f"value_{index} = compute(index={index}, scale=2)\n" for index in range(count))


def test_split_lines_breaks_on_newline_only() -> None:
    assert split_lines("a\r\nb\u2028c\n\nd") == ["a\r\n", "b\u2028c\n", "\n", "d"]
    assert split_lines("") == []


def test_token_count_is_additive_over_lines() -> None:
    text = numbered_lines(30) + "def f(x):\n    return x  # done\n"
    assert count_tokens(text) == sum(count_tokens(line) for line in split_lines(text))
    assert count_tokens("") == 0
    assert count_tokens("identifier") < count_tokens("identifier(alpha, beta)")


def test_empty_and_blank_files_have_no_chunks() -> None:
    assert chunk_naive(source("")) == []
    assert chunk_naive(source("\n\n   \n")) == []


def test_small_file_is_one_window() -> None:
    text = "import os\n\nprint(os.getcwd())"
    [chunk] = chunk_naive(source(text))

    assert (chunk.start_line, chunk.end_line) == (1, 3)
    assert chunk.content == text
    assert chunk.strategy is Strategy.NAIVE
    assert chunk.kind is ChunkKind.WINDOW
    assert chunk.path == "src/app.py"
    assert chunk.language is Language.PYTHON
    assert chunk.symbol is None
    assert chunk.token_count == count_tokens(text)
    assert chunk.content_hash == hashlib.sha256(text.encode()).hexdigest()


def test_line_ranges_match_content_and_cover_the_file() -> None:
    text = numbered_lines(400)
    lines = split_lines(text)
    chunks = chunk_naive(source(text))

    assert len(chunks) > 1
    assert chunks[0].start_line == 1
    assert chunks[-1].end_line == len(lines)
    for chunk in chunks:
        assert chunk.content == "".join(lines[chunk.start_line - 1 : chunk.end_line])
    for previous, current in pairwise(chunks):
        assert previous.start_line < current.start_line <= previous.end_line + 1


def test_windows_stay_within_budget_and_overlap() -> None:
    lines = split_lines(numbered_lines(400))
    chunks = chunk_naive(source("".join(lines)))

    for chunk in chunks:
        assert chunk.token_count <= WINDOW_TOKENS
    for previous, current in pairwise(chunks):
        shared = lines[current.start_line - 1 : previous.end_line]
        assert shared, "consecutive windows must overlap"
        assert count_tokens("".join(shared)) <= OVERLAP_TOKENS
        # The overlap is as large as fits: one more line would exceed it.
        one_more = lines[current.start_line - 2 : previous.end_line]
        assert count_tokens("".join(one_more)) > OVERLAP_TOKENS
    # Windows are filled: adding the next line would exceed the budget.
    for chunk in chunks[:-1]:
        grown = lines[chunk.start_line - 1 : chunk.end_line + 1]
        assert count_tokens("".join(grown)) > WINDOW_TOKENS


def test_oversized_line_becomes_its_own_window() -> None:
    long_line = " + ".join(f"term_{index}" for index in range(400)) + "\n"
    text = "first = 1\n" + long_line + "last = 2\n"
    chunks = chunk_naive(source(text))

    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [(1, 1), (2, 2), (3, 3)]
    assert chunks[1].token_count > WINDOW_TOKENS


@pytest.mark.parametrize(("window", "overlap"), [(5, 0), (20, 3), (50, 49)])
def test_windows_always_move_forward(window: int, overlap: int) -> None:
    text = numbered_lines(60)
    chunks = chunk_naive(source(text), window_tokens=window, overlap_tokens=overlap)

    starts = [chunk.start_line for chunk in chunks]
    assert starts == sorted(set(starts))
    assert chunks[-1].end_line == 60


def test_crlf_lines_keep_their_numbering() -> None:
    text = "a = 1\r\nb = 2\r\nc = 3\r\n"
    [chunk] = chunk_naive(source(text))

    assert (chunk.start_line, chunk.end_line) == (1, 3)
    assert chunk.content == text
