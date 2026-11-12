"""Citation parser: map ``[Sx]`` markers to their sources and drop the ones that do not exist."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from repolens.generation.context import Source

# One bracket with one or more labels: [S1], [S1, S3], [S1,S2]. Whitespace before it goes too.
_MARKER = re.compile(r"[ \t]*\[(S\d+(?:\s*,\s*S\d+)*)\]")


@dataclass(frozen=True, slots=True)
class Citation:
    label: str
    path: str
    start_line: int
    end_line: int

    @property
    def location(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


@dataclass(frozen=True, slots=True)
class ParsedAnswer:
    answer: str
    """The answer with every invalid label removed."""
    citations: list[Citation]
    """Valid cited sources, in order of first use."""
    invalid_labels: list[str]
    """Labels the answer used that match no source, in order of first use."""


def parse_citations(answer: str, sources: Sequence[Source]) -> ParsedAnswer:
    by_label = {source.label: source for source in sources}
    citations: dict[str, Citation] = {}
    invalid: dict[str, None] = {}

    def keep_valid(match: re.Match[str]) -> str:
        labels = [label.strip() for label in match.group(1).split(",")]
        valid = []
        for label in labels:
            source = by_label.get(label)
            if source is None:
                invalid.setdefault(label)
                continue
            valid.append(label)
            citations.setdefault(
                label, Citation(label, source.path, source.start_line, source.end_line)
            )
        if not valid:
            return ""
        prefix = match.group(0)[: match.start(1) - match.start(0) - 1]
        return f"{prefix}[{', '.join(valid)}]"

    cleaned = _MARKER.sub(keep_valid, answer)
    return ParsedAnswer(
        answer=cleaned, citations=list(citations.values()), invalid_labels=list(invalid)
    )
