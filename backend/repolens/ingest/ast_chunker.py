"""AST strategy: one chunk per function, method and class, parsed with tree-sitter.

Each chunk starts with a context header naming the file, the symbol and the imports the chunk
uses. Line ranges are whole source lines, and the content after the header is exactly those
lines, so a chunk can be cited as ``path:start-end``:

- A definition's range includes the comments and decorators directly above it.
- Methods are chunks of their own. A class chunk holds the class lines no method or nested class
  covers (signature, docstring, fields), so no line is embedded twice. Code outside any
  definition (imports, constants, top-level statements) forms module chunks the same way.
  When definitions split such lines into several runs, each run is a chunk, trimmed of blank
  lines and skipped when it holds only punctuation such as a closing brace.
- Nested functions stay inside their enclosing chunk.
- Size rules: a chunk under ``MERGE_TOKENS`` joins an adjacent sibling in the same scope (only
  blank lines between them, the result within ``SPLIT_TOKENS``); a chunk over ``SPLIT_TOKENS``
  is split where its child statements start, and every part keeps the header. A single
  statement larger than that stays whole.

Token counts for the size rules cover the code, not the header. The parser recovers from syntax
errors, so invalid code yields chunks for the definitions it can still recognise and never
raises.
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
from repolens.ingest.tokens import count_tokens, split_lines
from repolens.ingest.walker import Language, SourceFile

SPLIT_TOKENS = 800
MERGE_TOKENS = 40

_FUNCTION_VALUES = frozenset({"arrow_function", "function_expression", "generator_function"})
_JS_FUNCTIONS = frozenset({"function_declaration", "generator_function_declaration"})
_JS_CLASSES = frozenset({"class_declaration", "abstract_class_declaration"})
_JS_FIELDS = frozenset({"public_field_definition", "field_definition"})
_LEADING = frozenset({"comment", "decorator"})
_BLOCKS = frozenset({"block", "statement_block"})
_HAS_WORD = re.compile(r"\w")


@dataclass(frozen=True, slots=True)
class _Piece:
    """One symbol a chunk holds; a merged chunk holds several."""

    kind: ChunkKind
    symbol: str | None
    owner: str | None
    """The class a method or nested class belongs to."""
    lines: int


@dataclass(frozen=True, slots=True)
class _Span:
    pieces: tuple[_Piece, ...]
    start: int
    """First line index (0-based)."""
    end: int
    """Last line index (0-based), inclusive."""
    cuts: tuple[int, ...]
    """Line indices where a child statement starts, the places a split may cut."""


@dataclass(frozen=True, slots=True)
class _Definition:
    kind: ChunkKind
    symbol: str
    start: int
    end: int
    body: Node | None
    """A class's body, whose members become chunks of their own."""
    block: Node | None
    """A function's statement block, where an oversized chunk may be split."""


class _File:
    def __init__(self, source: SourceFile, *, split_tokens: int, merge_tokens: int) -> None:
        self.language = source.language
        self.lines = split_lines(source.text)
        self.tokens = [count_tokens(line) for line in self.lines]
        self.split_tokens = split_tokens
        self.merge_tokens = merge_tokens

    def size(self, start: int, end: int) -> int:
        return sum(self.tokens[start : end + 1])


def chunk_ast(
    source: SourceFile,
    *,
    split_tokens: int = SPLIT_TOKENS,
    merge_tokens: int = MERGE_TOKENS,
) -> list[Chunk]:
    """Chunk a TypeScript, JavaScript or Python file; other languages yield no chunks."""
    grammar = _grammar_for(source)
    if grammar is None:
        return []
    root = Parser(grammar).parse(source.text.encode("utf-8")).root_node
    file = _File(source, split_tokens=split_tokens, merge_tokens=merge_tokens)
    imports = _imports(root, source.language)
    comment = "#" if source.language is Language.PYTHON else "//"
    whole = _Piece(ChunkKind.MODULE, None, None, len(file.lines))

    chunks: list[Chunk] = []
    for span in _scope(file, root, whole, 0, len(file.lines) - 1, owner=None):
        parts = _split(file, span)
        for number, (start, end) in enumerate(parts, start=1):
            code = "".join(file.lines[start : end + 1])
            symbol = "; ".join(_describe(piece) for piece in span.pieces)
            if len(parts) > 1:
                symbol += f" (part {number} of {len(parts)})"
            header = [f"path: {source.path}", f"symbol: {symbol}"]
            used = [name for name in imports if _uses(code, name)]
            if used:
                header.append(f"imports: {', '.join(used)}")
            largest = max(span.pieces, key=lambda piece: piece.lines)
            names = [piece.symbol for piece in span.pieces if piece.symbol]
            chunks.append(
                make_chunk(
                    strategy=Strategy.AST,
                    source=source,
                    kind=largest.kind,
                    symbol=", ".join(dict.fromkeys(names)) or None,
                    start_line=start + 1,
                    end_line=end + 1,
                    content="".join(f"{comment} {line}\n" for line in header) + code,
                )
            )
    chunks.sort(key=lambda chunk: (chunk.start_line, chunk.end_line))
    return chunks


def _scope(
    file: _File, scope: Node, gap: _Piece, start: int, end: int, owner: str | None
) -> list[_Span]:
    """Chunk the lines ``start..end`` of a module or class whose members are in ``scope``.

    Functions, methods and the scope's own lines (``gap``) are siblings, so the merge rule
    applies to them; a nested class is chunked as a scope of its own.
    """
    siblings: list[_Span] = []
    nested: list[_Span] = []
    covered: set[int] = set()
    for node in scope.named_children:
        definition = _definition(node, file, owner)
        if definition is None:
            continue
        covered.update(range(definition.start, definition.end + 1))
        if definition.body is not None:
            inner = _Piece(ChunkKind.CLASS, definition.symbol, owner, 0)
            nested.extend(
                _scope(
                    file,
                    definition.body,
                    inner,
                    definition.start,
                    definition.end,
                    definition.symbol,
                )
            )
            continue
        cuts = _starts(file, definition.block, definition.start, definition.end)
        piece = _Piece(definition.kind, definition.symbol, owner, definition.end - definition.start)
        siblings.append(_Span((piece,), definition.start, definition.end, cuts))

    rest = [index for index in range(start, end + 1) if index not in covered]
    for run_start, run_end in _runs(rest):
        while run_start <= run_end and not file.lines[run_start].strip():
            run_start += 1
        while run_end >= run_start and not file.lines[run_end].strip():
            run_end -= 1
        if not any(_HAS_WORD.search(file.lines[i]) for i in range(run_start, run_end + 1)):
            continue
        piece = _Piece(gap.kind, gap.symbol, gap.owner, run_end - run_start)
        siblings.append(
            _Span((piece,), run_start, run_end, _starts(file, scope, run_start, run_end))
        )

    siblings.sort(key=lambda span: span.start)
    return _merge(file, siblings) + nested


def _merge(file: _File, siblings: list[_Span]) -> list[_Span]:
    """Join each chunk under ``merge_tokens`` with an adjacent sibling while the result fits."""
    merged: list[_Span] = []
    for span in siblings:
        if merged:
            previous = merged[-1]
            small = min(file.size(previous.start, previous.end), file.size(span.start, span.end))
            adjacent = not any(file.lines[i].strip() for i in range(previous.end + 1, span.start))
            fits = file.size(previous.start, span.end) <= file.split_tokens
            if small < file.merge_tokens and adjacent and fits:
                merged[-1] = _Span(
                    previous.pieces + span.pieces,
                    previous.start,
                    span.end,
                    (*previous.cuts, span.start, *span.cuts),
                )
                continue
        merged.append(span)
    return merged


def _split(file: _File, span: _Span) -> list[tuple[int, int]]:
    """Cut a span over ``split_tokens`` into parts at its cut points, packing them greedily."""
    if file.size(span.start, span.end) <= file.split_tokens:
        return [(span.start, span.end)]
    bounds = sorted({span.start, *(c for c in span.cuts if span.start < c <= span.end)})
    units = list(zip(bounds, [*bounds[1:], span.end + 1], strict=True))
    parts: list[tuple[int, int]] = []
    part_start, part_end = units[0][0], units[0][1] - 1
    for unit_start, unit_stop in units[1:]:
        if file.size(part_start, unit_stop - 1) <= file.split_tokens:
            part_end = unit_stop - 1
        else:
            parts.append((part_start, part_end))
            part_start, part_end = unit_start, unit_stop - 1
    parts.append((part_start, part_end))

    trimmed: list[tuple[int, int]] = []
    for start, end in parts:
        while start < end and not file.lines[start].strip():
            start += 1
        while end > start and not file.lines[end].strip():
            end -= 1
        trimmed.append((start, end))
    return trimmed


def _starts(file: _File, block: Node | None, start: int, end: int) -> tuple[int, ...]:
    """Rows within ``start..end`` where a child statement of ``block`` starts.

    A statement starts at the comments and decorators directly above it, so a split never
    separates them.
    """
    if block is None:
        return ()
    rows: set[int] = set()
    for child in block.named_children:
        if child.type in _LEADING:
            continue
        row = child.start_point.row
        sibling = child.prev_named_sibling
        while (
            sibling is not None
            and sibling.type in _LEADING
            and sibling.end_point.row >= row - 1
            and _starts_line(sibling, file.lines)
        ):
            row = sibling.start_point.row
            sibling = sibling.prev_named_sibling
        rows.add(row)
    return tuple(sorted(row for row in rows if start < row <= end))


def _definition(node: Node, file: _File, owner: str | None) -> _Definition | None:
    """Recognise ``node`` as a function, method or class, or return None."""
    target, outer = node, node
    if file.language is Language.PYTHON:
        if node.type == "decorated_definition":
            target = node.child_by_field_name("definition") or node
        if target.type == "function_definition":
            kind = ChunkKind.FUNCTION if owner is None else ChunkKind.METHOD
            block = target.child_by_field_name("body")
            return _build(outer, file, kind, _name(target, "name"), None, block, owner)
        if target.type == "class_definition":
            body = target.child_by_field_name("body")
            return _build(outer, file, ChunkKind.CLASS, _name(target, "name"), body, None, owner)
        return None

    if node.type == "export_statement":
        target = node.child_by_field_name("declaration") or node
    if owner is None:
        if target.type in _JS_FUNCTIONS:
            block = target.child_by_field_name("body")
            name = _name(target, "name")
            return _build(outer, file, ChunkKind.FUNCTION, name, None, block, owner)
        if target.type in _JS_CLASSES:
            body = target.child_by_field_name("body")
            name = _name(target, "name")
            return _build(outer, file, ChunkKind.CLASS, name, body, None, owner)
        if target.type in ("lexical_declaration", "variable_declaration"):
            declarators = [c for c in target.named_children if c.type == "variable_declarator"]
            if len(declarators) == 1:
                value = declarators[0].child_by_field_name("value")
                if value is not None and value.type in _FUNCTION_VALUES:
                    name = _name(declarators[0], "name")
                    block = _block(value)
                    return _build(outer, file, ChunkKind.FUNCTION, name, None, block, owner)
        return None

    if node.type == "method_definition":
        block = node.child_by_field_name("body")
        return _build(outer, file, ChunkKind.METHOD, _name(node, "name"), None, block, owner)
    if node.type in _JS_FIELDS:
        value = node.child_by_field_name("value")
        if value is not None and value.type in _FUNCTION_VALUES:
            name = _name(node, "name") or _name(node, "property")
            return _build(outer, file, ChunkKind.METHOD, name, None, _block(value), owner)
    return None


def _block(function: Node) -> Node | None:
    body = function.child_by_field_name("body")
    return body if body is not None and body.type in _BLOCKS else None


def _build(
    node: Node,
    file: _File,
    kind: ChunkKind,
    name: str | None,
    body: Node | None,
    block: Node | None,
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
        and _starts_line(sibling, file.lines)
    ):
        start = sibling.start_point.row
        sibling = sibling.prev_named_sibling
    end = node.end_point.row
    if node.end_point.column == 0 and end > start:
        end -= 1  # the node ends at the start of the next line
    symbol = f"{owner}.{name}" if owner else name
    return _Definition(kind, symbol, start, end, body, block)


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


def _describe(piece: _Piece) -> str:
    if piece.kind is ChunkKind.MODULE:
        return "module-level code"
    if piece.owner is None:
        return f"{piece.symbol} ({piece.kind})"
    if piece.kind is ChunkKind.CLASS:
        return f"{piece.symbol} (class nested in class {piece.owner})"
    return f"{piece.symbol} (method of class {piece.owner})"


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
