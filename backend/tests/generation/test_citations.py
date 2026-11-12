import pytest

from repolens.generation.citations import Citation, parse_citations
from repolens.generation.context import Source

SOURCES = [
    Source("S1", "src/auth.py", 10, 12, "verify"),
    Source("S2", "src/routes.py", 1, 8, "routes"),
    Source("S3", "README.md", 3, 9, "docs"),
]


def test_valid_markers_map_to_their_locations_in_order_of_first_use() -> None:
    parsed = parse_citations("Routes are in routes.py [S2]; tokens in auth.py [S1, S2].", SOURCES)

    assert parsed.answer == "Routes are in routes.py [S2]; tokens in auth.py [S1, S2]."
    assert parsed.citations == [
        Citation("S2", "src/routes.py", 1, 8),
        Citation("S1", "src/auth.py", 10, 12),
    ]
    assert [citation.location for citation in parsed.citations] == [
        "src/routes.py:1-8",
        "src/auth.py:10-12",
    ]
    assert parsed.invalid_labels == []


def test_invalid_markers_are_removed_and_flagged() -> None:
    parsed = parse_citations("It is in auth.py [S7]. Also here [S1, S9].", SOURCES)

    assert parsed.answer == "It is in auth.py. Also here [S1]."
    assert parsed.citations == [Citation("S1", "src/auth.py", 10, 12)]
    assert parsed.invalid_labels == ["S7", "S9"]


@pytest.mark.parametrize(
    ("answer", "cleaned", "labels"),
    [
        ("A [S1][S3].", "A [S1][S3].", ["S1", "S3"]),
        ("A [S1,S3].", "A [S1, S3].", ["S1", "S3"]),
        ("A [ S1 ].", "A [ S1 ].", []),
        ("A [s1].", "A [s1].", []),
        ("A [Source 1].", "A [Source 1].", []),
    ],
)
def test_marker_formats(answer: str, cleaned: str, labels: list[str]) -> None:
    parsed = parse_citations(answer, SOURCES)

    assert parsed.answer == cleaned
    assert [citation.label for citation in parsed.citations] == labels


def test_answer_without_citations() -> None:
    parsed = parse_citations("not found in the indexed code", SOURCES)

    assert parsed.answer == "not found in the indexed code"
    assert parsed.citations == []
    assert parsed.invalid_labels == []


def test_every_marker_is_invalid_when_there_are_no_sources() -> None:
    parsed = parse_citations("Made up [S1].", [])

    assert parsed.answer == "Made up."
    assert parsed.invalid_labels == ["S1"]


def test_text_that_only_looks_like_code_is_untouched() -> None:
    answer = "Index with values[S] or matrix[S1x]."

    assert parse_citations(answer, SOURCES).answer == answer
