"""AST strategy: one chunk per function, method and class, parsed with tree-sitter.

Each chunk starts with a context header naming the file, the symbol and the imports the chunk
uses. Line ranges are whole source lines, so they can be cited as ``path:start-end``:

- A definition's range includes the comments and decorators directly above it.
- Methods are chunks of their own. A class chunk holds the class lines no method or nested class
  covers (signature, docstring, fields), so no line is embedded twice; when methods split those
  lines into several runs, each run is a chunk, trimmed of blank lines and skipped when it holds
  only punctuation such as a closing brace.
- Nested functions stay inside their enclosing chunk. Code outside any definition (imports,
  constants, top-level statements) is not emitted here.

The parser recovers from syntax errors, so invalid code yields chunks for the definitions it
can still recognise and never raises.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import PurePosixPath

import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language as Grammar
from tree_sitter import Node, Parser

from repolens.ingest.chunk import Chunk, ChunkKind, Strategy, make_chunk
from repolens.ingest.tokens import split_lines
from repolens.ingest.walker import Language, SourceFile

_FUNCTION_VALUES = frozenset({"arrow_function", "function_expression", "generator_function"})
_JS_FUNCTIONS = frozenset({"function_declaration", "generator_function_declaration"})
_JS_CLASSES = frozenset({"class_declaration", "abstract_class_declaration"})
_JS_FIELDS = frozenset({"public_field_definition", "field_definition"})
_LEADING = frozenset({"comment", "decorator"})
_HAS_WORD = re.compile(r"\w")


@dataclass(frozen=True, slots=True)
class _Definition:
    kind: ChunkKind
    symbol: str
    start: int
    """First line index (0-based), including leading comments and decorators."""
    end: int
    """Last line index (0-based), inclusive."""
    body: Node | None
    """A class's body, whose members become chunks of their own."""
    owner: str | None
    """The enclosing class of a method."""


def chunk_ast(source: SourceFile) -> list[Chunk]:
    """Chunk a TypeScript, JavaScript or Python file; other languages yield no chunks."""
    grammar = _grammar_for(source)
    if grammar is None:
        return []
    root = Parser(grammar).parse(source.text.encode("utf-8")).root_node
    lines = split_lines(source.text)
    imports = _imports(root, source.language)
    comment = "#" if source.language is Language.PYTHON else "//"

    chunks: list[Chunk] = []
    for kind, symbol, owner, start, end in _spans(root, source.language, lines):
        code = "".join(lines[start : end + 1])
        header = [f"path: {source.path}", f"symbol: {_describe(kind, symbol, owner)}"]
        used = [name for name in imports if _uses(code, name)]
        if used:
            header.append(f"imports: {', '.join(used)}")
        content = "".join(f"{comment} {line}\n" for line in header) + code
        chunks.append(
            make_chunk(
                strategy=Strategy.AST,
                source=source,
                kind=kind,
                symbol=symbol,
                start_line=start + 1,
                end_line=end + 1,
                content=content,
            )
        )
    chunks.sort(key=lambda chunk: (chunk.start_line, chunk.end_line))
    return chunks


def _spans(
    scope: Node, language: Language, lines: list[str], owner: str | None = None
) -> Iterator[tuple[ChunkKind, str, str | None, int, int]]:
    """Yield ``(kind, symbol, owner, start, end)`` for the definitions directly in ``scope``."""
    for node in scope.named_children:
        definition = _definition(node, language, lines, owner)
        if definition is None:
            continue
        if definition.body is None:
            yield definition.kind, definition.symbol, owner, definition.start, definition.end
            continue

        covered: set[int] = set()
        for member in _spans(definition.body, language, lines, definition.symbol):
            covered.update(range(member[3], member[4] + 1))
            yield member
        rest = [i for i in range(definition.start, definition.end + 1) if i not in covered]
        for start, end in _runs(rest):
            while start <= end and not lines[start].strip():
                start += 1
            while end >= start and not lines[end].strip():
                end -= 1
            if any(_HAS_WORD.search(lines[i]) for i in range(start, end + 1)):
                yield ChunkKind.CLASS, definition.symbol, owner, start, end


def _definition(
    node: Node, language: Language, lines: list[str], owner: str | None
) -> _Definition | None:
    """Recognise ``node`` as a function, method or class, or return None."""
    target, outer = node, node
    if language is Language.PYTHON:
        if node.type == "decorated_definition":
            target = node.child_by_field_name("definition") or node
        if target.type == "function_definition":
            kind = ChunkKind.FUNCTION if owner is None else ChunkKind.METHOD
            return _build(outer, lines, kind, _name(target, "name"), None, owner)
        if target.type == "class_definition":
            body = target.child_by_field_name("body")
            return _build(outer, lines, ChunkKind.CLASS, _name(target, "name"), body, owner)
        return None

    if node.type == "export_statement":
        target = node.child_by_field_name("declaration") or node
    if owner is None:
        if target.type in _JS_FUNCTIONS:
            return _build(outer, lines, ChunkKind.FUNCTION, _name(target, "name"), None, owner)
        if target.type in _JS_CLASSES:
            body = target.child_by_field_name("body")
            return _build(outer, lines, ChunkKind.CLASS, _name(target, "name"), body, owner)
        if target.type in ("lexical_declaration", "variable_declaration"):
            declarators = [c for c in target.named_children if c.type == "variable_declarator"]
            if len(declarators) == 1:
                value = declarators[0].child_by_field_name("value")
                if value is not None and value.type in _FUNCTION_VALUES:
                    name = _name(declarators[0], "name")
                    return _build(outer, lines, ChunkKind.FUNCTION, name, None, owner)
        return None

    if node.type == "method_definition":
        return _build(outer, lines, ChunkKind.METHOD, _name(node, "name"), None, owner)
    if node.type in _JS_FIELDS:
        value = node.child_by_field_name("value")
        if value is not None and value.type in _FUNCTION_VALUES:
            name = _name(node, "name") or _name(node, "property")
            return _build(outer, lines, ChunkKind.METHOD, name, None, owner)
    return None


def _build(
    node: Node,
    lines: list[str],
    kind: ChunkKind,
    name: str | None,
    body: Node | None,
    owner: str | None,
) -> _Definition | None:
    if not name:
        return None  # error recovery can leave a definition without its name
    start = node.start_point.row
    sibling = node.prev_named_sibling
    while (
        sibling is not None
        and sibling.type in _LEADING
        and sibling.end_point.row >= start - 1
        and _starts_line(sibling, lines)
    ):
        start = sibling.start_point.row
        sibling = sibling.prev_named_sibling
    end = node.end_point.row
    if node.end_point.column == 0 and end > start:
        end -= 1  # the node ends at the start of the next line
    symbol = f"{owner}.{name}" if owner else name
    return _Definition(kind, symbol, start, end, body, owner)


def _starts_line(node: Node, lines: list[str]) -> bool:
    """Whether only whitespace precedes ``node`` on its line, unlike a trailing comment."""
    row, column = node.start_point
    line = lines[row].encode("utf-8") if row < len(lines) else b""
    return not line[:column].strip()


def _name(node: Node, field: str) -> str | None:
    child = node.child_by_field_name(field)
    if child is None or child.is_missing or child.text is None:
        return None
    return child.text.decode("utf-8", errors="replace")


def _describe(kind: ChunkKind, symbol: str, owner: str | None) -> str:
    if owner is None:
        return f"{symbol} ({kind})"
    if kind is ChunkKind.CLASS:
        return f"{symbol} (class nested in class {owner})"
    return f"{symbol} (method of class {owner})"


def _runs(indices: list[int]) -> Iterator[tuple[int, int]]:
    """Group sorted line indices into inclusive ``(start, end)`` runs."""
    start = previous = None
    for index in indices:
        if start is None or previous is None:
            start = previous = index
        elif index == previous + 1:
            previous = index
        else:
            yield start, previous
            start = previous = index
    if start is not None and previous is not None:
        yield start, previous


def _imports(root: Node, language: Language) -> list[str]:
    """Local names the file imports, in order of first appearance."""
    names: dict[str, None] = {}
    types = (
        ("import_statement", "import_from_statement")
        if language is Language.PYTHON
        else ("import_statement",)
    )
    for statement in _descendants(root, types):
        if language is Language.PYTHON:
            for child in statement.children_by_field_name("name"):
                bound = child.child_by_field_name("alias") or child
                if bound.text is not None:
                    names.setdefault(bound.text.decode("utf-8", errors="replace"))
        else:
            clause = next((c for c in statement.named_children if c.type == "import_clause"), None)
            if clause is not None:
                for name in _bound_names(clause):
                    names.setdefault(name)
    return list(names)


def _bound_names(clause: Node) -> Iterator[str]:
    """The identifiers a JavaScript or TypeScript import clause binds."""
    for child in clause.named_children:
        if child.type == "identifier" and child.text is not None:
            yield child.text.decode("utf-8", errors="replace")
        elif child.type == "namespace_import":
            yield from _bound_names(child)
        elif child.type == "named_imports":
            for specifier in child.named_children:
                bound = specifier.child_by_field_name("alias") or specifier.child_by_field_name(
                    "name"
                )
                if specifier.type == "import_specifier" and bound and bound.text is not None:
                    yield bound.text.decode("utf-8", errors="replace")


def _descendants(node: Node, types: tuple[str, ...]) -> Iterator[Node]:
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in types:
            yield current
            continue
        stack.extend(reversed(current.named_children))


def _uses(code: str, name: str) -> bool:
    """Whether ``code`` mentions an imported name; a dotted import counts by its first part."""
    head = name.split(".", 1)[0]
    return re.search(rf"(?<![\w$]){re.escape(head)}(?![\w$])", code) is not None


def _grammar_for(source: SourceFile) -> Grammar | None:
    match source.language:
        case Language.PYTHON:
            return _grammar("python")
        case Language.JAVASCRIPT:
            return _grammar("javascript")
        case Language.TYPESCRIPT:
            tsx = PurePosixPath(source.path).suffix.lower() == ".tsx"
            return _grammar("tsx" if tsx else "typescript")
        case _:
            return None


@cache
def _grammar(name: str) -> Grammar:
    match name:
        case "python":
            return Grammar(tree_sitter_python.language())
        case "javascript":
            return Grammar(tree_sitter_javascript.language())
        case "tsx":
            return Grammar(tree_sitter_typescript.language_tsx())
        case _:
            return Grammar(tree_sitter_typescript.language_typescript())
