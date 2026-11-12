from repolens.ingest.chunk import Chunk, ChunkKind, Strategy
from repolens.ingest.markdown import chunk_markdown
from repolens.ingest.tokens import count_tokens
from repolens.ingest.walker import Language, SourceFile


def chunks_of(text: str) -> list[Chunk]:
    return chunk_markdown(SourceFile(path="README.md", language=Language.MARKDOWN, text=text))


def spans(chunks: list[Chunk]) -> list[tuple[int, int]]:
    return [(chunk.start_line, chunk.end_line) for chunk in chunks]


DOC = """\
# RepoLens

Intro paragraph.

## Install

Run the installer.

### Docker

docker compose up

### From source

uv sync

## Usage

Ask a question.
"""


def test_sections_split_on_h2_and_h3_with_heading_paths() -> None:
    chunks = chunks_of(DOC)

    assert spans(chunks) == [(1, 4), (5, 8), (9, 12), (13, 16), (17, 19)]
    assert [chunk.content.split("\n", 1)[0] for chunk in chunks[1:]] == [
        "Install",
        "Install > Docker",
        "Install > From source",
        "Usage",
    ]
    assert chunks[2].content == "Install > Docker\n\n### Docker\n\ndocker compose up\n\n"


def test_preamble_has_no_heading_path() -> None:
    preamble = chunks_of(DOC)[0]

    assert preamble.content == "# RepoLens\n\nIntro paragraph.\n\n"


def test_chunk_fields() -> None:
    chunk = chunks_of(DOC)[1]

    assert chunk.strategy is Strategy.MARKDOWN
    assert chunk.kind is ChunkKind.SECTION
    assert chunk.path == "README.md"
    assert chunk.language is Language.MARKDOWN
    assert chunk.symbol is None
    assert chunk.token_count == count_tokens(chunk.content)


def test_file_without_headings_is_one_chunk() -> None:
    text = "# Title\n\nJust text.\n#### Minor heading\nMore text."
    [chunk] = chunks_of(text)

    assert (chunk.start_line, chunk.end_line) == (1, 5)
    assert chunk.content == text


def test_empty_and_blank_files_have_no_chunks() -> None:
    assert chunks_of("") == []
    assert chunks_of("\n  \n") == []


def test_file_starting_with_a_heading_has_no_preamble() -> None:
    assert spans(chunks_of("## A\n\ntext\n## B\n")) == [(1, 3), (4, 4)]


def test_h3_before_any_h2_uses_its_own_title() -> None:
    [chunk] = chunks_of("### Notes\n\ntext\n")

    assert chunk.content.startswith("Notes\n\n### Notes\n")


def test_new_h2_resets_the_heading_path() -> None:
    chunks = chunks_of("## A\n### B\n## C\n### D\n")

    assert [chunk.content.split("\n", 1)[0] for chunk in chunks] == ["A", "A > B", "C", "C > D"]


def test_headings_inside_fenced_code_do_not_split() -> None:
    text = (
        "## Build\n"
        "```bash\n"
        "## compile everything\n"
        "make\n"
        "```\n"
        "~~~~\n"
        "### still code\n"
        "~~~\n"
        "### still code too\n"
        "~~~~\n"
        "## Test\n"
    )

    assert spans(chunks_of(text)) == [(1, 10), (11, 11)]


def test_heading_syntax_rules() -> None:
    text = (
        "##NoSpace is text\n"
        "   ## Indented ##\n"
        "    ## code block, not a heading\n"
        "#### too deep\n"
        "## \n"
        "### Child\n"
    )
    chunks = chunks_of(text)

    assert spans(chunks) == [(1, 1), (2, 4), (5, 5), (6, 6)]
    assert [chunk.content.split("\n", 1)[0] for chunk in chunks[1:]] == ["Indented", "## ", "Child"]
