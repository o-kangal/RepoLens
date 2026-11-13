import re
from pathlib import Path

import pytest

from repolens.ingest.ast_chunker import chunk_ast
from repolens.ingest.chunk import Chunk, ChunkKind, Strategy
from repolens.ingest.tokens import split_lines
from repolens.ingest.walker import Language, SourceFile, detect_language

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ast"


def source_of(name: str) -> SourceFile:
    language = detect_language(name)
    assert language is not None
    return SourceFile(path=f"src/{name}", language=language, text=(FIXTURES / name).read_text())


def chunks_of(name: str) -> list[Chunk]:
    return chunk_ast(source_of(name))


def outline(chunks: list[Chunk]) -> list[tuple[str, str | None, int, int]]:
    return [(chunk.kind, chunk.symbol, chunk.start_line, chunk.end_line) for chunk in chunks]


HEADER_LINE = re.compile(r"(#|//) (path|symbol|imports): ")


def header(chunk: Chunk) -> list[str]:
    """The leading ``path``, ``symbol`` and ``imports`` lines of a chunk."""
    lines: list[str] = []
    for line in chunk.content.splitlines():
        if not HEADER_LINE.match(line):
            break
        lines.append(line)
    return lines


def code(chunk: Chunk) -> str:
    """The chunk content after its header."""
    lines = chunk.content.splitlines(keepends=True)
    return "".join(lines[len(header(chunk)) :])


def test_typescript_classes_methods_and_arrow_functions() -> None:
    chunks = chunks_of("services.ts")

    assert outline(chunks) == [
        ("class", "UserService", 7, 10),
        ("method", "UserService.findById", 12, 18),
        ("method", "UserService.handle", 20, 20),
        ("function", "slugify", 23, 24),
        ("function", "add", 26, 28),
    ]
    assert {chunk.strategy for chunk in chunks} == {Strategy.AST}


def test_header_names_path_symbol_and_the_imports_the_chunk_uses() -> None:
    by_symbol = {chunk.symbol: chunk for chunk in chunks_of("services.ts")}

    assert header(by_symbol["UserService.findById"]) == [
        "// path: src/services.ts",
        "// symbol: UserService.findById (method of class UserService)",
        "// imports: prisma, NotFoundError",
    ]
    assert header(by_symbol["UserService.handle"])[2] == "// imports: path"
    assert header(by_symbol["UserService"])[2] == "// imports: Log"
    # No imported name appears in add, so it gets no imports line.
    assert header(by_symbol["add"]) == ["// path: src/services.ts", "// symbol: add (function)"]


def test_leading_comments_and_decorators_belong_to_their_definition() -> None:
    by_symbol = {chunk.symbol: chunk for chunk in chunks_of("services.ts")}

    assert code(by_symbol["UserService"]).startswith(
        "/** Loads users from the database. */\n@Injectable()\nexport class UserService {\n"
    )
    assert code(by_symbol["UserService.findById"]).startswith(
        "  // Cached for a minute.\n  @Cached()\n  async findById("
    )


def test_a_class_chunk_holds_only_the_lines_its_methods_do_not() -> None:
    chunks = chunks_of("services.ts")
    lines = [line for chunk in chunks for line in range(chunk.start_line, chunk.end_line + 1)]

    assert len(lines) == len(set(lines)), "no source line is chunked twice"
    assert "findById" not in code(chunks[0])


def test_chunk_content_after_the_header_is_exactly_its_line_range() -> None:
    for name in ("services.ts", "Button.tsx", "legacy.js", "models.py"):
        source = source_of(name)
        lines = split_lines(source.text)
        for chunk in chunk_ast(source):
            assert code(chunk) == "".join(lines[chunk.start_line - 1 : chunk.end_line]), (
                name,
                chunk.symbol,
            )


def test_tsx_components() -> None:
    chunks = chunks_of("Button.tsx")

    assert outline(chunks) == [("function", "Button", 4, 6), ("function", "Counter", 8, 11)]
    assert header(chunks[0])[2] == "// imports: ButtonProps"
    assert header(chunks[1])[2] == "// imports: useState"
    assert "<button onClick={onPress}>" in code(chunks[0])


def test_javascript_generators_function_expressions_and_classes() -> None:
    assert outline(chunks_of("legacy.js")) == [
        ("function", "ids", 3, 6),
        ("function", "format", 8, 10),
        ("class", "Queue", 12, 13),
        ("method", "Queue.push", 15, 18),
    ]


def test_python_decorators_methods_and_nested_classes() -> None:
    chunks = chunks_of("models.py")

    assert outline(chunks) == [
        ("function", "top_level", 7, 8),
        ("class", "User", 11, 16),
        ("method", "User.slug", 18, 20),
        ("class", "User.Meta", 22, 23),
        ("class", "User", 25, 25),
        ("function", "load", 28, 32),
    ]
    by_lines = {(chunk.start_line, chunk.symbol): chunk for chunk in chunks}
    assert code(by_lines[11, "User"]).startswith("@dataclass(frozen=True)\nclass User:\n")
    assert code(by_lines[18, "User.slug"]).startswith("    @property\n    def slug(")
    assert header(by_lines[22, "User.Meta"])[1] == (
        "# symbol: User.Meta (class nested in class User)"
    )
    assert header(by_lines[11, "User"])[2] == "# imports: dataclass, dc_field"
    assert header(by_lines[7, "top_level"])[2] == "# imports: os.path"


def test_a_comment_trailing_code_is_not_a_leading_comment() -> None:
    top_level = chunks_of("models.py")[0]

    assert top_level.start_line == 7
    assert "VERSION" not in top_level.content


def test_nested_functions_stay_inside_their_enclosing_chunk() -> None:
    load = chunks_of("models.py")[-1]

    assert load.symbol == "load"
    assert "def helper()" in code(load)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("broken.py", ["ok", "broken", "inner", "after"]),
        ("broken.ts", ["ok", "Broken", "later"]),
    ],
)
def test_invalid_code_does_not_crash_and_keeps_the_definitions_it_can_read(
    name: str, expected: list[str]
) -> None:
    chunks = chunks_of(name)

    assert [chunk.symbol for chunk in chunks] == expected
    assert chunks[0].kind == ChunkKind.FUNCTION
    assert "still parsed" in code(chunks[-1])


@pytest.mark.parametrize("text", ["", "\n\n", "x = 1\n", "\x00\x01 def (", "class"])
def test_files_without_definitions_or_with_garbage_yield_no_chunks(text: str) -> None:
    assert chunk_ast(SourceFile(path="a.py", language=Language.PYTHON, text=text)) == []


def test_markdown_is_not_parsed() -> None:
    source = SourceFile(path="README.md", language=Language.MARKDOWN, text="## Title\n\nText\n")

    assert chunk_ast(source) == []
