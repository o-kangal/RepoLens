"""Markdown strategy: one chunk per ``##`` or ``###`` section, headed by its heading path."""

import re
from dataclasses import dataclass

from repolens.ingest.chunk import Chunk, ChunkKind, Strategy, make_chunk
from repolens.ingest.tokens import split_lines
from repolens.ingest.walker import SourceFile

_HEADING = re.compile(r" {0,3}(#{2,3})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*\n?")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
HEADING_SEPARATOR = " > "


@dataclass(frozen=True, slots=True)
class _Section:
    start: int
    """Index of the section's first line."""
    heading_path: str


def chunk_markdown(source: SourceFile) -> list[Chunk]:
    """Split ``source`` on ATX ``##`` and ``###`` headings outside fenced code blocks.

    Each chunk's content starts with its heading path ("Install > Docker"), then the section's
    lines. Text before the first such heading, or a file without one, forms a chunk without a
    heading path.
    """
    lines = split_lines(source.text)
    sections = _find_sections(lines)
    chunks: list[Chunk] = []

    for index, section in enumerate(sections):
        end = sections[index + 1].start if index + 1 < len(sections) else len(lines)
        body = "".join(lines[section.start : end])
        if not body.strip():
            continue
        content = f"{section.heading_path}\n\n{body}" if section.heading_path else body
        chunks.append(
            make_chunk(
                strategy=Strategy.MARKDOWN,
                source=source,
                kind=ChunkKind.SECTION,
                start_line=section.start + 1,
                end_line=end,
                content=content,
            )
        )
    return chunks


def _find_sections(lines: list[str]) -> list[_Section]:
    sections = [_Section(start=0, heading_path="")]
    current_h2: str | None = None
    fence: str | None = None

    for index, line in enumerate(lines):
        fence_match = _FENCE.match(line)
        if fence is not None:
            if fence_match and _closes(fence_match.group(1), fence, line):
                fence = None
            continue
        if fence_match:
            fence = fence_match.group(1)
            continue

        heading = _HEADING.fullmatch(line)
        if heading is None:
            continue
        title = (heading.group(2) or "").strip()
        if len(heading.group(1)) == 2:
            current_h2 = title
            path = title
        else:
            path = HEADING_SEPARATOR.join(part for part in (current_h2, title) if part)
        sections.append(_Section(start=index, heading_path=path))

    return sections


def _closes(marker: str, fence: str, line: str) -> bool:
    """A closing fence uses the opening character, is at least as long, and has no info string."""
    return (
        marker[0] == fence[0]
        and len(marker) >= len(fence)
        and not line.strip().removeprefix(marker).strip()
    )
