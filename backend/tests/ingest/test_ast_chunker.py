import re
from pathlib import Path

import pytest

from repolens.ingest.ast_chunker import MERGE_TOKENS, SPLIT_TOKENS, chunk_ast
from repolens.ingest.chunk import Chunk, ChunkKind, Strategy
from repolens.ingest.tokens import count_tokens, split_lines
from repolens.ingest.walker import Language, SourceFile, detect_language

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ast"
FIXTURE_NAMES = ["services.ts", "Button.tsx", "legacy.js", "models.py", "broken.py", "broken.ts"]
# Structure tests turn merging off, since the fixtures are small enough to merge almost whole.
STRUCTURE = {"merge_tokens": 0}
SETTINGS = [
    pytest.param({}, id="defaults"),
    pytest.param(STRUCTURE, id="no-merge"),
    pytest.param({"split_tokens": 30, "merge_tokens": 0}, id="split-30"),
    pytest.param({"split_tokens": 60, "merge_tokens": 1000}, id="merge-all-within-60"),
]

HEADER_LINE = re.compile(r"(#|//) (path|symbol|imports): ")


def source_of(name: str) -> SourceFile:
    language = detect_language(name)
    assert language is not None
    return SourceFile(path=f"src/{name}", language=language, text=(FIXTURES / name).read_text())


def python(text: str) -> SourceFile:
    return SourceFile(path="app.py", language=Language.PYTHON, text=text)


def chunks_of(name: str, **settings: int) -> list[Chunk]:
    return chunk_ast(source_of(name), **settings)


def outline(chunks: list[Chunk]) -> list[tuple[str, str | None, int, int]]:
    return [(chunk.kind, chunk.symbol, chunk.start_line, chunk.end_line) for chunk in chunks]


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


# Structure: one chunk per function, method and class, plus module code.


def test_typescript_classes_methods_arrow_functions_and_module_code() -> None:
    chunks = chunks_of("services.ts", **STRUCTURE)

    assert outline(chunks) == [
        ("module", None, 1, 5),
        ("class", "UserService", 7, 10),
        ("method", "UserService.findById", 12, 18),
        ("method", "UserService.handle", 20, 20),
        ("function", "slugify", 23, 24),
        ("function", "add", 26, 28),
    ]
    assert {chunk.strategy for chunk in chunks} == {Strategy.AST}
    assert code(chunks[0]).startswith('import { prisma } from "./db";\n')
    assert code(chunks[0]).endswith("const LIMIT = 10;\n")


def test_header_names_path_symbol_and_the_imports_the_chunk_uses() -> None:
    by_symbol = {chunk.symbol: chunk for chunk in chunks_of("services.ts", **STRUCTURE)}

    assert header(by_symbol["UserService.findById"]) == [
        "// path: src/services.ts",
        "// symbol: UserService.findById (method of class UserService)",
        "// imports: prisma, NotFoundError",
    ]
    assert header(by_symbol["UserService.handle"])[2] == "// imports: path"
    assert header(by_symbol["UserService"])[2] == "// imports: Log"
    assert header(by_symbol[None])[1] == "// symbol: module-level code"
    # No imported name appears in add, so it gets no imports line.
    assert header(by_symbol["add"]) == ["// path: src/services.ts", "// symbol: add (function)"]


def test_leading_comments_and_decorators_belong_to_their_definition() -> None:
    by_symbol = {chunk.symbol: chunk for chunk in chunks_of("services.ts", **STRUCTURE)}

    assert code(by_symbol["UserService"]).startswith(
        "/** Loads users from the database. */\n@Injectable()\nexport class UserService {\n"
    )
    assert code(by_symbol["UserService.findById"]).startswith(
        "  // Cached for a minute.\n  @Cached()\n  async findById("
    )


def test_tsx_components() -> None:
    chunks = chunks_of("Button.tsx", **STRUCTURE)

    assert outline(chunks) == [
        ("module", None, 1, 2),
        ("function", "Button", 4, 6),
        ("function", "Counter", 8, 11),
    ]
    assert header(chunks[1])[2] == "// imports: ButtonProps"
    assert header(chunks[2])[2] == "// imports: useState"
    assert "<button onClick={onPress}>" in code(chunks[1])


def test_javascript_generators_function_expressions_and_classes() -> None:
    assert outline(chunks_of("legacy.js", **STRUCTURE)) == [
        ("module", None, 1, 1),
        ("function", "ids", 3, 6),
        ("function", "format", 8, 10),
        ("class", "Queue", 12, 13),
        ("method", "Queue.push", 15, 18),
        ("module", None, 21, 21),
    ]


def test_python_decorators_methods_and_nested_classes() -> None:
    chunks = chunks_of("models.py", **STRUCTURE)

    assert outline(chunks) == [
        ("module", None, 1, 6),
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


def test_a_comment_trailing_code_stays_with_the_code() -> None:
    module, top_level = chunks_of("models.py", **STRUCTURE)[:2]

    assert code(module).endswith("VERSION = 2  # bump on schema change\n")
    assert top_level.start_line == 7
    assert "VERSION" not in top_level.content


def test_nested_functions_stay_inside_their_enclosing_chunk() -> None:
    load = chunks_of("models.py", **STRUCTURE)[-1]

    assert load.symbol == "load"
    assert "def helper()" in code(load)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("broken.py", ["ok", "broken", None, "inner", "after"]),
        ("broken.ts", ["ok", "Broken", "later"]),
    ],
)
def test_invalid_code_does_not_crash_and_keeps_the_definitions_it_can_read(
    name: str, expected: list[str | None]
) -> None:
    chunks = chunks_of(name, **STRUCTURE)

    assert [chunk.symbol for chunk in chunks] == expected
    assert chunks[0].kind == ChunkKind.FUNCTION
    assert "still parsed" in code(chunks[-1])


@pytest.mark.parametrize("text", ["", "\n\n", "}\n", "  ;\n"])
def test_files_without_code_yield_no_chunks(text: str) -> None:
    assert chunk_ast(python(text)) == []


@pytest.mark.parametrize("text", ["x = 1\n", "\x00\x01 def (\n", "class\n"])
def test_top_level_code_and_garbage_form_a_module_chunk(text: str) -> None:
    assert outline(chunk_ast(python(text))) == [("module", None, 1, 1)]


def test_markdown_is_not_parsed() -> None:
    source = SourceFile(path="README.md", language=Language.MARKDOWN, text="## Title\n\nText\n")

    assert chunk_ast(source) == []


# Size rules: merge small siblings, split large chunks on child statements.


def test_small_siblings_merge_into_one_chunk() -> None:
    chunks = chunks_of("services.ts")

    assert outline(chunks) == [
        ("module", None, 1, 5),
        ("method", "UserService, UserService.findById, UserService.handle", 7, 20),
        ("function", "slugify, add", 23, 28),
    ]
    assert header(chunks[2])[1] == "// symbol: slugify (function); add (function)"
    assert header(chunks[1])[1] == (
        "// symbol: UserService (class); UserService.findById (method of class UserService); "
        "UserService.handle (method of class UserService)"
    )


def test_merging_needs_adjacent_siblings_in_the_same_scope() -> None:
    chunks = chunks_of("legacy.js")

    # module.exports is small, but the class Queue lies between it and the functions above.
    assert outline(chunks) == [
        ("function", "ids, format", 1, 10),
        ("method", "Queue, Queue.push", 12, 18),
        ("module", None, 21, 21),
    ]
    # A nested class is a scope of its own, so User.Meta does not merge into User's chunks.
    assert ("class", "User.Meta", 22, 23) in outline(chunks_of("models.py"))


def test_a_merge_never_exceeds_the_split_size() -> None:
    for name in FIXTURE_NAMES:
        for chunk in chunks_of(name, split_tokens=60, merge_tokens=1000):
            if "; " in header(chunk)[1]:  # merged from several symbols
                assert count_tokens(code(chunk)) <= 60, (name, chunk.symbol)


REPORT = '''\
import json


def report(rows):
    """Summarise rows."""
    total = sum(row["amount"] for row in rows)
    count = len(rows)

    # Average per row.
    average = total / count if count else 0
    return json.dumps({"total": total, "count": count, "average": average})
'''


def test_a_large_function_splits_on_statements_and_every_part_keeps_the_header() -> None:
    chunks = chunk_ast(python(REPORT), split_tokens=40, merge_tokens=0)

    assert outline(chunks) == [
        ("module", None, 1, 1),
        ("function", "report", 4, 7),
        ("function", "report", 9, 11),
    ]
    first, second = chunks[1:]
    assert header(first) == ["# path: app.py", "# symbol: report (function) (part 1 of 2)"]
    assert header(second) == [
        "# path: app.py",
        "# symbol: report (function) (part 2 of 2)",
        "# imports: json",
    ]
    # The comment stays with the statement below it.
    assert code(second).startswith("    # Average per row.\n    average = ")


def test_a_statement_larger_than_the_limit_is_not_cut() -> None:
    entries = "".join(f'    "key_{index}": {index},\n' for index in range(40))
    text = f"def table():\n    return {{\n{entries}    }}\n"

    parts = chunk_ast(python(text), split_tokens=40, merge_tokens=0)

    assert outline(parts) == [("function", "table", 1, 1), ("function", "table", 2, 43)]


def test_large_classes_and_module_code_split_on_their_members() -> None:
    fields = "".join(f"    field_{index}: int = {index}\n" for index in range(12))
    constants = "".join(f"LIMIT_{index} = {index}\n" for index in range(12))
    text = f"{constants}\n\nclass Settings:\n{fields}"

    chunks = chunk_ast(python(text), split_tokens=40, merge_tokens=0)

    module = [chunk for chunk in chunks if chunk.kind == ChunkKind.MODULE]
    classes = [chunk for chunk in chunks if chunk.kind == ChunkKind.CLASS]
    assert len(module) > 1
    assert len(classes) > 1
    assert all(count_tokens(code(chunk)) <= 40 for chunk in module + classes)
    assert [header(chunk)[1] for chunk in classes][-1].endswith(
        f"(part {len(classes)} of {len(classes)})"
    )


def test_default_limits_match_the_spec() -> None:
    assert (SPLIT_TOKENS, MERGE_TOKENS) == (800, 40)


# Line ranges stay exact under every setting.


@pytest.mark.parametrize("settings", SETTINGS)
@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_content_after_the_header_is_exactly_the_line_range(
    name: str, settings: dict[str, int]
) -> None:
    source = source_of(name)
    lines = split_lines(source.text)
    for chunk in chunk_ast(source, **settings):
        assert code(chunk) == "".join(lines[chunk.start_line - 1 : chunk.end_line]), chunk.symbol


@pytest.mark.parametrize("settings", SETTINGS)
@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_every_line_with_code_is_in_exactly_one_chunk(name: str, settings: dict[str, int]) -> None:
    lines = split_lines(source_of(name).text)
    covered = [
        line
        for chunk in chunk_ast(source_of(name), **settings)
        for line in range(chunk.start_line, chunk.end_line + 1)
    ]

    assert len(covered) == len(set(covered))
    with_words = {number for number, line in enumerate(lines, 1) if re.search(r"\w", line)}
    assert with_words <= set(covered)
